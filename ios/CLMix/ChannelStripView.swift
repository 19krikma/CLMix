import SwiftUI

// Longest personal label the rename alert sends, matching the server's
// own MAX_CHANNEL_NAME cap (services/remote_server.py) so the text that
// goes out is the text that comes back. An alert's TextField takes no
// input filter, so unlike Android's dialog this is applied on the way
// out rather than as it is typed.
private let maxPersonalNameLength = 32

struct ChannelStripView: View {
    @EnvironmentObject var model: AppModel
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
    var onOpenInput: ((ChannelState) -> Void)? = nil

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

    private var hardMuted: Bool { model.isMixerMode && model.hardMute && channel.muted }

    var body: some View {
        VStack(spacing: 0) {
            heading

            faderRow

            if model.panSupported {
                tonalButton(PanFormat.buttonLabel(channel.pan)) {
                    showPanSheet = true
                }
                .padding(.top, 4)
            }

            if model.muteOffered {
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
                tonalButton("MUTE", active: channel.muted, pulsed: hardMuted) {
                    model.setMute(channel: channel.channel, muted: !channel.muted)
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
                onChange: { pan in model.setPan(channel: channel.channel, pan: pan) }
            )
            .presentationDetents([.height(280)])
        }
        .alert("Rename for you only", isPresented: $showRenameAlert) {
            TextField("Channel name", text: $nameDraft)
                .textInputAutocapitalization(.characters)

            Button("Save") {
                let trimmed = nameDraft
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                model.setPersonalName(
                    channel: channel.channel,
                    name: String(trimmed.prefix(maxPersonalNameLength))
                )
            }

            // An empty name is how the server is asked to drop the
            // label, so this is a real action rather than a second
            // Cancel - it puts the console's own name back.
            Button("Reset") {
                model.setPersonalName(channel: channel.channel, name: "")
            }

            Button("Cancel", role: .cancel) {}
        } message: {
            if let snapshot = model.liveSnapshot {
                Text("Your name for this channel on \"\(snapshot)\". "
                     + "Nobody else sees it, and the mixer is not changed.")
            } else {
                Text("Your name for this channel. Nobody else sees it, "
                     + "and the mixer is not changed.")
            }
        }
        // Switching to a mono aux with the sheet already open would
        // otherwise leave a pan control on screen for a bus that has no
        // pan axis - mirrors Android's applyAuxWidth dismissing it.
        .onChange(of: model.panSupported) { _, supported in
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
                    guard model.personalizationAllowed else { return }
                    nameDraft = channel.name
                    showRenameAlert = true
                }
        }
    }

    private var headingLabel: some View {
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
            // fader below takes whatever height is left, so a strip whose
            // name wraps would otherwise end up with a shorter fader than
            // its neighbours and a Mute button sitting at a different
            // height along the row.
            Text(channel.name)
                .font(.system(size: 12, weight: .bold))
                .multilineTextAlignment(.center)
                .lineLimit(2, reservesSpace: true)
                .truncationMode(.tail)
                .foregroundStyle(Color.clmixOnSurface)
                .frame(maxWidth: .infinity)
                .padding(.bottom, 4)
        }
        .contentShape(Rectangle())
    }

    /// The ruler, the fader and the meter, sharing whatever height the
    /// name and the buttons leave - mirrors Android's fader_row taking
    /// layout_weight="1", so a row of strips always has its Mute buttons
    /// at the same height whatever the names above them did.
    ///
    /// The negative margins either side are the point of the layout: the
    /// fader's touch band is far wider than the track drawn down the
    /// middle of it, and both the tick lines and the meter were being
    /// held out at arm's length by that dead width. Overlapping costs
    /// nothing - the ruler's lines and the meter both draw over
    /// transparent space, and the meter takes no touches, so the fader
    /// underneath still gets them.
    private var faderRow: some View {
        HStack(spacing: 0) {
            LevelRulerView()
                .frame(width: 31)
                .padding(.trailing, -5)

            LevelFaderView(
                db: channel.level ?? AuxTaper.bottomDb,
                fineMode: fineMode,
                onChange: { db in model.setLevel(channel: channel.channel, db: db) }
            )
            .frame(width: 30)

            // Beside the fader rather than against its ruler: this is
            // the console's own 0..-60 dB scale, not the fader's
            // -150..+10, and the two are not interchangeable.
            ChannelMeterView(channel: channel.channel, stereo: channel.stereo)
                .frame(width: 10)
                .padding(.leading, -8)
                .allowsHitTesting(false)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func tonalButton(
        _ title: String, active: Bool = false, pulsed: Bool = false,
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
        .background { buttonFill(active: active, pulsed: pulsed) }
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    @ViewBuilder
    private func buttonFill(active: Bool, pulsed: Bool) -> some View {
        if pulsed {
            HardMutePulse()
        } else {
            active ? Color.clmixMuteActive : Color.clmixMuteInactive
        }
    }
}

/// Breathes between the mute red and a dimmed version of the same red
/// while a hard mute is in force.
///
/// A hard mute is not only down in the room but out of every monitor mix
/// too - a far bigger thing to have done by accident than an ordinary
/// mute, so a strip muted under it moves rather than sitting still.
/// Deliberately a fade rather than a blink: it has to register in
/// peripheral vision across a row of strips without becoming the thing
/// the eye keeps snapping back to during a show. Fading towards the
/// inactive grey instead would read as the mute releasing, so it fades
/// towards the same hue at lower brightness.
///
/// A view of its own so the repeating animation starts from its own
/// onAppear: driven off a flag on the strip, it would only ever run for
/// strips that were already muted when the screen was built, and sit
/// frozen on whichever end it started at for the rest.
private struct HardMutePulse: View {
    // One breath in or out; a full cycle is twice this.
    private static let pulseSeconds = 0.75

    @State private var dimmed = false

    var body: some View {
        // The dim is faded in over the full red rather than the two
        // colours being swapped: an opacity change interpolates, where
        // exchanging one Color view for another can simply cut.
        Color.clmixMuteActive
            .overlay(Color.clmixMuteActiveDim.opacity(dimmed ? 1 : 0))
            .animation(
                .easeInOut(duration: Self.pulseSeconds).repeatForever(autoreverses: true),
                value: dimmed
            )
            .onAppear { dimmed = true }
    }
}
