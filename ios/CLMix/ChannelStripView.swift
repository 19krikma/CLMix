import SwiftUI

// Longest personal label the rename alert sends, matching the server's
// own MAX_CHANNEL_NAME cap (services/remote_server.py) so the text that
// goes out is the text that comes back. An alert's TextField takes no
// input filter, so unlike Android's dialog this is applied on the way
// out rather than as it is typed.
private let maxPersonalNameLength = 32

/// One channel's column: name, ruler, fader, meter, pan and mute.
///
/// Everything it draws arrives as a value and everything it does leaves
/// through a closure - deliberately, where every other screen in this app
/// reads `AppModel` out of the environment. A view observing the model
/// rebuilds when *any* of its published properties changes, and there are
/// a dozen of these on screen at once: a status message landing in the
/// top bar, or a snapshot name arriving with a levels frame, would
/// otherwise re-lay-out twelve rulers, twelve faders and twelve meters to
/// change something none of them draw. Taking values instead, and
/// comparing them in `==` below, is what lets the grid rebuild only the
/// strips that actually moved. Android gets the same effect from
/// RecyclerView rebinding single positions.
struct ChannelStripView: View, Equatable {
    let channel: ChannelState
    let fineMode: Bool
    // Alternates a subtle background so adjacent strips read as visually
    // separate columns instead of blurring together - mirrors Android's
    // ChannelAdapter tinting odd positions with R.color.surface_variant.
    var alternate: Bool = false
    // Whether this strip names the channel's number on the console above
    // its name, and lets that heading open the channel's input stage.
    // Off for the aux screens, which have always shown the name alone and
    // have no input stage to reach; on for Full Mixer Control, where the
    // whole console is in reach and the number is how the desk itself
    // refers to a strip.
    var showChannelNumber: Bool = false
    // Full Mixer Control moves the meter up beside the channel name, as a
    // small bar the height of the name, and gives the fader row back the
    // space it took. The aux screens keep it beside the fader. Mirrors
    // Android's ChannelAdapter.meterBesideName.
    var meterBesideName: Bool = false

    // AppModel.panSupported / .muteOffered, resolved by the screen above.
    let panSupported: Bool
    let muteOffered: Bool
    var personalizationAllowed: Bool = false
    var liveSnapshot: String? = nil

    let onLevel: (Double) -> Void
    let onPan: (Double) -> Void
    let onMute: (Bool) -> Void
    var onPersonalName: ((String) -> Void)? = nil
    var onOpenInput: ((ChannelState) -> Void)? = nil
    // Full Mixer Control only: the EQ button under the name, which opens
    // the channel's EQ screen. Nil everywhere else, where the button is
    // not drawn - an aux mix has no EQ of its own to reach from a strip.
    var onOpenEq: ((ChannelState) -> Void)? = nil
    // Full Mixer Control only, like onOpenEq: COMP and GATE under the EQ,
    // opening the Dynamics screen on the compressor (true) or gate.
    var onOpenDynamics: ((ChannelState, Bool) -> Void)? = nil

    @State private var showPanSheet = false
    @State private var showRenameAlert = false

    // Seeded from the strip's current name each time the alert opens, so
    // editing starts from what is on screen rather than from an empty
    // field or the last channel renamed.
    @State private var nameDraft = ""

    /// 76pt, down from the 118 this started at. Most of that came out of
    /// dead space rather than out of the controls: the fader's touch band
    /// is far wider than the track drawn down the middle of it, and the
    /// strip used to be sized around the band. Five full strips now fit
    /// on a phone that showed three and a half.
    private static let width: CGFloat = 76

    /// Compares what the strip draws, and nothing else. The action
    /// closures are new objects on every evaluation of the parent's body
    /// and so can never compare equal - but they only ever reach back
    /// into AppModel, which is a reference type, so a closure held over
    /// from a skipped rebuild still acts on current state.
    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.channel == rhs.channel
            && lhs.fineMode == rhs.fineMode
            && lhs.alternate == rhs.alternate
            && lhs.showChannelNumber == rhs.showChannelNumber
            && lhs.meterBesideName == rhs.meterBesideName
            && lhs.panSupported == rhs.panSupported
            && lhs.muteOffered == rhs.muteOffered
            && lhs.personalizationAllowed == rhs.personalizationAllowed
            && lhs.liveSnapshot == rhs.liveSnapshot
            // Not state, but it decides whether the heading is a button
            // onto the input sheet or plain text.
            && (lhs.onOpenInput == nil) == (rhs.onOpenInput == nil)
            && (lhs.onOpenEq == nil) == (rhs.onOpenEq == nil)
            && (lhs.onOpenDynamics == nil) == (rhs.onOpenDynamics == nil)
    }

    var body: some View {
        VStack(spacing: 0) {
            heading

            // The channel's EQ curve in miniature, and the button that
            // opens it full size. Between the name and the fader, so the
            // fader gives up the height rather than the buttons along the
            // bottom moving.
            if let onOpenEq {
                Button { onOpenEq(channel) } label: {
                    EqThumbView(eq: channel.eq)
                        .frame(maxWidth: .infinity)
                        .frame(height: 40)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("EQ")
                .padding(.bottom, 6)
            }

            // The channel's dynamics in miniature: the compressor's curve
            // over the gate's, stacked so each keeps the strip's full
            // width. Drawn in the accent colour while that section is in;
            // a tap opens the Dynamics screen on it.
            if let onOpenDynamics {
                Button { onOpenDynamics(channel, true) } label: {
                    DynThumbView(comp: true, section: channel.dynamics?.comp)
                        .frame(maxWidth: .infinity)
                        .frame(height: 34)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Compressor")
                .padding(.bottom, 4)

                Button { onOpenDynamics(channel, false) } label: {
                    DynThumbView(comp: false, section: channel.dynamics?.gate)
                        .frame(maxWidth: .infinity)
                        .frame(height: 34)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Gate")
                .padding(.bottom, 6)
            }

            faderRow

            if panSupported {
                tonalButton(PanFormat.buttonLabel(channel.pan)) {
                    showPanSheet = true
                }
                .padding(.top, 4)
            }

            if muteOffered {
                // The label stays "MUTE" in both states - it names the
                // button, it does not report the state. Colour carries
                // that, which reads faster across a row of strips than
                // four characters on each, and stops the label changing
                // width as it toggles.
                //
                // A tap flips the button straight away rather than
                // waiting for the console's echo (see AppModel.setMute) -
                // the server stays the authority on what's actually
                // muted.
                tonalButton("MUTE", active: channel.muted) {
                    onMute(!channel.muted)
                }
                .padding(.top, 6)
            }
        }
        .padding(.horizontal, 4)
        .frame(width: Self.width)
        .frame(maxHeight: .infinity)
        .background(alternate ? Color.clmixSurfaceVariant : Color.clear)
        .sheet(isPresented: $showPanSheet) {
            PanSheetView(
                channelName: channel.name,
                pan: channel.pan ?? 0,
                onChange: onPan
            )
            .presentationDetents([.height(280)])
        }
        .alert("Rename for you only", isPresented: $showRenameAlert) {
            TextField("Channel name", text: $nameDraft)
                .textInputAutocapitalization(.characters)

            Button("Save") {
                let trimmed = nameDraft
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                onPersonalName?(String(trimmed.prefix(maxPersonalNameLength)))
            }

            // An empty name is how the server is asked to drop the
            // label, so this is a real action rather than a second
            // Cancel - it puts the console's own name back.
            Button("Reset") {
                onPersonalName?("")
            }

            Button("Cancel", role: .cancel) {}
        } message: {
            if let liveSnapshot {
                Text("Your name for this channel on \"\(liveSnapshot)\". "
                     + "Nobody else sees it, and the mixer is not changed.")
            } else {
                Text("Your name for this channel. Nobody else sees it, "
                     + "and the mixer is not changed.")
            }
        }
        // Switching to a mono aux with the sheet already open would
        // otherwise leave a pan control on screen for a bus that has no
        // pan axis - mirrors Android's applyAuxWidth dismissing it.
        .onChange(of: panSupported) { _, supported in
            if !supported { showPanSheet = false }
        }
    }

    /// The channel's number over its name. The two are one target
    /// together, of a comfortable size at the top of the strip, rather
    /// than two small digits on their own - and only a control at all
    /// where there is an input stage behind it to open. Elsewhere it is
    /// plain text that swallows nothing.
    @ViewBuilder
    private var heading: some View {
        if showChannelNumber, let onOpenInput {
            Button { onOpenInput(channel) } label: { headingLabel }
                .buttonStyle(.plain)
        } else {
            // A long press here relabels the strip for this account
            // alone. Deliberately on this branch only: it is the one the
            // aux screens take, and in Full Mixer Control the heading is
            // already a button onto the input sheet, where renaming a
            // channel renames it on the console for everyone.
            //
            // The permission is checked inside rather than by attaching
            // this conditionally - a conditional modifier would change
            // the view's identity, and nothing else here consumes a long
            // press, so an account without it simply finds the gesture
            // does nothing. Mirrors Android's
            // ChannelAdapter.onNameLongPressed.
            headingLabel
                .onLongPressGesture {
                    guard personalizationAllowed else { return }
                    nameDraft = channel.name
                    showRenameAlert = true
                }
        }
    }

    /// The number over the name, with the meter beside both on Full
    /// Mixer Control - a small bar running from the number down to the
    /// foot of the name, out of the way of the fader below.
    private var headingLabel: some View {
        HStack(alignment: .top, spacing: 3) {
            VStack(spacing: 0) {
                if showChannelNumber {
                    Text("\(channel.channel)")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.clmixOnSurfaceVariant)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 5)
                }

                // Two lines always, not just when a name needs them: the
                // fader below takes whatever height is left, so a strip
                // whose name wraps would otherwise end up with a shorter
                // fader than its neighbours and a Mute button sitting at a
                // different height along the row.
                Text(channel.name)
                    .font(.system(size: 12, weight: .bold))
                    .multilineTextAlignment(.center)
                    .lineLimit(2, reservesSpace: true)
                    .truncationMode(.tail)
                    .foregroundStyle(Color.clmixOnSurface)
                    .frame(maxWidth: .infinity)
            }

            if meterBesideName {
                ChannelMeterView(channel: channel.channel, stereo: channel.stereo)
                    .frame(width: 7)
                    .frame(maxHeight: .infinity)
                    .padding(.top, 4)
                    .allowsHitTesting(false)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(.bottom, 4)
        .contentShape(Rectangle())
    }

    /// The ruler, the fader and the meter, sharing whatever height the
    /// name and the buttons leave - mirrors Android's fader_row taking
    /// layout_weight="1", so a row of strips always has its Mute buttons
    /// at the same height whatever the names above them did.
    ///
    /// The negative margins either side are purely visual: the tick
    /// lines and the meter sit close against the track instead of being
    /// held out at arm's length by dead space. They cost nothing to draw
    /// over - but two things keep that overlap from being felt as well as
    /// seen. The meter takes no touches at all (below), and LevelFaderView
    /// trims its own hit-test rectangle to exclude exactly these
    /// overlapped slivers, so a tap that lands on a ruler number or the
    /// meter never reads as a fader touch. zIndex keeps the thumb itself
    /// drawn on top of the meter rather than sliced by it where the two
    /// overlap.
    private var faderRow: some View {
        HStack(spacing: 0) {
            // .equatable() because the ruler has no inputs at all: it is
            // the same twenty-odd positioned labels and lines on every
            // strip and in every state, and laying them out again is pure
            // cost. See LevelRulerView.
            LevelRulerView()
                .equatable()
                .frame(width: 31)
                .padding(.trailing, -5)

            LevelFaderView(
                db: channel.level ?? AuxTaper.bottomDb,
                fineMode: fineMode,
                onChange: onLevel
            )
            .frame(width: 30)
            .zIndex(1)

            // Beside the fader rather than against its ruler: this is
            // the console's own 0..-60 dB scale, not the fader's
            // -150..+10, and the two are not interchangeable.
            if !meterBesideName {
                ChannelMeterView(channel: channel.channel, stereo: channel.stereo)
                    .frame(width: 10)
                    .padding(.leading, -8)
                    .allowsHitTesting(false)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func tonalButton(
        _ title: String, active: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 11))
                .lineLimit(1)
                .frame(maxWidth: .infinity)
                .frame(height: 34)
        }
        .foregroundStyle(active ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
        .background(active ? Color.clmixMuteActive : Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}
