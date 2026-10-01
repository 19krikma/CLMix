import SwiftUI

/// A channel's input stage: its name, which input feeds it, and the
/// head-amp gain and digital trim, each on a dial.
///
/// Opened from the channel number above the name on the Full Mixer
/// Control strips. Both values are console-wide - a head amp feeds FOH,
/// every monitor and the recording at once - which is why they live
/// behind a sheet of their own rather than on the strip, and why the
/// dials have to be held to move (see DialView).
///
/// Mirrors Android's ChannelInputBottomSheet.kt.
struct ChannelInputSheet: View {
    /// Live: the mixer-control screen passes the channel fresh on every
    /// push, so the sheet keeps up with the console while it is open.
    let channel: ChannelState
    let onGainChanged: (Int, Double) -> Void
    let onTrimChanged: (Int, Double) -> Void
    let onPhantomChanged: (Int, Bool) -> Void
    let onPhaseChanged: (Int, Int) -> Void
    /// What each dial sweeps, in dB - one range per parameter, as the
    /// server states them at login (see MixerClient.gainRange).
    let gainRange: ClosedRange<Double>
    let trimRange: ClosedRange<Double>
    let onNameChanged: (Int, String) -> Void

    // What each dial is showing. Nil until the console has answered for
    // this channel, which is what the dials read as "nothing to show
    // yet" rather than as 0 dB.
    @State private var gainShown: Double?
    @State private var trimShown: Double?

    // When each dial was last turned by hand. A push arriving right after
    // a turn still carries the console's pre-turn value (it has to travel
    // to the desk and back), and writing that into the dial would drag it
    // backwards under the finger - so pushes are ignored briefly after a
    // turn, the same bargain the channel strips strike for their faders.
    @State private var gainTouchedAt = Date.distantPast
    @State private var trimTouchedAt = Date.distantPast

    // When each dial last sent a write. A dial turns at the display's own
    // rate, and sending every one of those frames would put ~60 writes a
    // second per dial on the console for a gesture the operator
    // experiences as one move. Held to writeInterval while turning; the
    // value it settles on is always sent, so the console never ends up on
    // a rounded-off intermediate.
    @State private var gainSentAt = Date.distantPast
    @State private var trimSentAt = Date.distantPast

    // What the 48V and polarity buttons are showing. Flipped on tap
    // rather than waiting for the console's echo - the same bargain the
    // strip's Mute button strikes - and pushes are ignored until they
    // agree or flagConfirm passes, so a frame already in flight cannot
    // flip one back.
    @State private var phantomShown = false
    @State private var phantomExpected: Bool?
    @State private var phantomSentAt = Date.distantPast

    // The polarity state being shown, as the console's own value rather
    // than a flag: 0 normal, 1...3 inverted (see ChannelState.phase).
    @State private var phaseShown = PhaseState.normal
    @State private var phaseExpected: Int?
    @State private var phaseSentAt = Date.distantPast

    // The inverted state to go back to when polarity is switched on again.
    // A stereo channel has three of them and this app cannot tell them
    // apart, so it remembers the one the desk reported instead of assuming
    // PhaseState.inverted - otherwise tapping polarity off and on would
    // quietly move which leg is inverted.
    @State private var phaseLastInverted = PhaseState.inverted

    // A rename takes a moment to reach the console and come back. Until
    // it does, pushes still carry the old name, and writing that into the
    // box would undo what was just typed in front of the user.
    @State private var nameShown = ""
    @State private var nameExpected: String?
    @State private var nameSentAt = Date.distantPast

    @State private var renaming = false
    @State private var draftName = ""
    @State private var inputNote = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Capsule()
                .fill(Color.clmixTrackBackground)
                .frame(width: 40, height: 4)
                .frame(maxWidth: .infinity)
                .padding(.bottom, 18)

            Text("Channel \(channel.channel)")
                .font(.system(size: 13))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
                .frame(maxWidth: .infinity)
                .padding(.bottom, 6)

            nameRow
                .padding(.bottom, 16)

            inputRow
                .padding(.bottom, 18)

            dialRow(
                label: "Gain",
                value: gainShown,
                range: gainRange,
                onChanged: { value, force in
                    gainTouchedAt = Date()
                    if force || gainTouchedAt.timeIntervalSince(gainSentAt) >= Self.writeInterval {
                        gainSentAt = gainTouchedAt
                        onGainChanged(channel.channel, value)
                    }
                },
                onShown: { gainShown = $0 }
            )
            .padding(.bottom, 14)

            dialRow(
                label: "Trim",
                value: trimShown,
                range: trimRange,
                onChanged: { value, force in
                    trimTouchedAt = Date()
                    if force || trimTouchedAt.timeIntervalSince(trimSentAt) >= Self.writeInterval {
                        trimSentAt = trimTouchedAt
                        onTrimChanged(channel.channel, value)
                    }
                },
                onShown: { trimShown = $0 }
            )
        }
        .padding(.horizontal, 24)
        .padding(.top, 10)
        .padding(.bottom, 28)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.clmixSurface)
        .onAppear(perform: seed)
        .onChange(of: channel) { _, state in fold(state) }
        .alert("Rename channel \(channel.channel)", isPresented: $renaming) {
            TextField("Name", text: $draftName)
                .textInputAutocapitalization(.characters)
            Button("Cancel", role: .cancel) {}
            Button("Rename") { commitRename() }
        }
    }

    // MARK: - Name

    /// The name reads as a value the console holds, like gain and trim
    /// below, rather than as a heading - so it wears the same readout
    /// box. Hold it to rewrite it; a plain tap does nothing, since the
    /// name is what the whole desk calls this channel and a stray touch
    /// should not open it for editing.
    private var nameRow: some View {
        HStack(spacing: 12) {
            Text(nameShown)
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(Color.clmixReadoutText)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .padding(.horizontal, 10)
                .background { readoutBox }
                .contentShape(Rectangle())
                .onLongPressGesture {
                    draftName = nameShown
                    renaming = true
                }

            phaseButton
        }
    }

    /// Polarity - the button fills with the app's accent when inverted
    /// rather than the warning red 48V uses, since nothing here can damage
    /// a microphone; it just sounds wrong.
    ///
    /// Any non-zero state is drawn the same way: a stereo channel's 1, 2
    /// and 3 each invert something, and which is which has never been
    /// established, so the button says "inverted" and the number is
    /// remembered rather than interpreted.
    private var phaseButton: some View {
        let inverted = phaseShown != PhaseState.normal

        return Button {
            let target = inverted ? PhaseState.normal : phaseLastInverted
            phaseExpected = target
            phaseSentAt = Date()
            phaseShown = target
            onPhaseChanged(channel.channel, target)
        } label: {
            PolaritySymbol()
                .stroke(
                    inverted ? Color.clmixOnPrimary : Color.clmixOnMuteInactive,
                    style: StrokeStyle(lineWidth: 1.9, lineCap: .round)
                )
                .frame(width: 24, height: 24)
                .frame(width: 56, height: 56)
        }
        .background(inverted ? Color.clmixPrimary : Color.clmixMuteInactive)
        .clipShape(Circle())
        .accessibilityLabel(inverted ? "Polarity inverted" : "Polarity normal")
    }

    private func commitRename() {
        let name = draftName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != nameShown else { return }

        nameExpected = name
        nameSentAt = Date()
        nameShown = name
        onNameChanged(channel.channel, String(name.prefix(Self.maxNameLength)))
    }

    // MARK: - Input and 48V

    /// Input names where the channel listens; 48V feeds the mic sitting
    /// there. They belong on one line because they are the same decision
    /// made twice - what is plugged in, and whether it needs powering.
    private var inputRow: some View {
        VStack(spacing: 8) {
            HStack(spacing: 12) {
                tonalButton("Input") {
                    withAnimation { inputNote = true }

                    Task { @MainActor in
                        try? await Task.sleep(nanoseconds: 3_000_000_000)
                        withAnimation { inputNote = false }
                    }
                }

                // 48V on reads as the console's own warning colour, not
                // the app's accent: it is the one control on this sheet
                // that can damage a source (ribbon mics especially), so
                // it should look like a live state rather than a
                // selected option.
                tonalButton("48V", active: phantomShown, activeFill: .clmixMuteActive) {
                    let target = !phantomShown
                    phantomExpected = target
                    phantomSentAt = Date()
                    phantomShown = target
                    onPhantomChanged(channel.channel, target)
                }
                .accessibilityLabel(phantomShown ? "48V on" : "48V off")
            }

            // The rack/port patch itself is not in the console's control
            // surface as probed (see docs/mixer_protocol) - nothing under
            // the channel input names a socket - so the button says so
            // rather than pretending to route something.
            if inputNote {
                Text("Input patching isn't available from the console yet")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .transition(.opacity)
            }
        }
    }

    // MARK: - Dials

    /// Each dial's label sits directly over its readout, so the pair
    /// reads as one control.
    private func dialRow(
        label: String,
        value: Double?,
        range: ClosedRange<Double>,
        onChanged: @escaping (Double, Bool) -> Void,
        onShown: @escaping (Double) -> Void
    ) -> some View {
        HStack(spacing: 16) {
            Spacer(minLength: 0)

            DialView(
                value: value ?? range.lowerBound,
                range: range,
                hasValue: value != nil,
                onValueChanged: { turned in
                    onShown(turned)
                    onChanged(turned, false)
                },
                // Always sent, throttling or not: this is the value the
                // operator actually chose.
                onTurnFinished: { settled in
                    onShown(settled)
                    onChanged(settled, true)
                }
            )

            VStack(spacing: 3) {
                Text(label)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)

                Text(Self.formatDb(value))
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Color.clmixReadoutText)
                    .frame(width: 86)
                    .padding(.vertical, 8)
                    .background { readoutBox }
            }
        }
    }

    // MARK: - Folding in pushes

    private func seed() {
        gainShown = channel.gain
        trimShown = channel.trim
        phantomShown = channel.phantom
        phaseShown = channel.phase
        if channel.phase != PhaseState.normal { phaseLastInverted = channel.phase }
        nameShown = channel.name
    }

    /// Folds in a push from the server, unless that control has just been
    /// worked by hand.
    private func fold(_ state: ChannelState) {
        let now = Date()

        if let pending = nameExpected,
           state.name == pending || now.timeIntervalSince(nameSentAt) > Self.nameConfirm {
            nameExpected = nil
        }

        if nameExpected == nil, nameShown != state.name {
            nameShown = state.name
        }

        if let gain = state.gain, now.timeIntervalSince(gainTouchedAt) > Self.settle,
           differs(gainShown, gain) {
            gainShown = gain
        }

        if let trim = state.trim, now.timeIntervalSince(trimTouchedAt) > Self.settle,
           differs(trimShown, trim) {
            trimShown = trim
        }

        // Settled once the console agrees, or given up on if it never
        // does - at which point the console's own state wins.
        if let expected = phantomExpected,
           state.phantom == expected || now.timeIntervalSince(phantomSentAt) > Self.flagConfirm {
            phantomExpected = nil
        }

        if phantomExpected == nil, state.phantom != phantomShown {
            phantomShown = state.phantom
        }

        if let expected = phaseExpected,
           state.phase == expected || now.timeIntervalSince(phaseSentAt) > Self.flagConfirm {
            phaseExpected = nil
        }

        if phaseExpected == nil, state.phase != phaseShown {
            phaseShown = state.phase
        }

        if state.phase != PhaseState.normal { phaseLastInverted = state.phase }
    }

    private func differs(_ shown: Double?, _ incoming: Double) -> Bool {
        guard let shown else { return true }
        return abs(shown - incoming) >= 0.05
    }

    // MARK: - Furniture

    private var readoutBox: some View {
        RoundedRectangle(cornerRadius: 6, style: .continuous)
            .fill(Color.clmixReadoutFill)
            .overlay(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .stroke(Color.clmixReadoutStroke, lineWidth: 1)
            )
    }

    private func tonalButton(
        _ title: String, active: Bool = false, activeFill: Color = .clmixPrimary,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
        }
        .foregroundStyle(active ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
        .background(active ? activeFill : Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    // The dials' ranges are no longer constants here: each sweeps its own
    // parameter's range, which the server states at login and
    // HeadAmpRange defaults for. They used to share one span, the union
    // -40...+60, which gave gain 20 dB of travel the desk only ever clamps
    // away.


    // How long after a turn to keep ignoring pushes for that dial.
    private static let settle: TimeInterval = 0.7

    // How long a tapped 48V or polarity button holds its own state
    // before deferring to the console again.
    private static let flagConfirm: TimeInterval = 2

    // A rename travels further than a flag - through the desktop's cache
    // and back out on the next push - so it is given longer.
    private static let nameConfirm: TimeInterval = 4

    // Matches the server's own cap (MAX_CHANNEL_NAME).
    private static let maxNameLength = 32

    // Fast enough that the desk visibly tracks the dial, far below the
    // ~60 a second the turn itself generates.
    private static let writeInterval: TimeInterval = 0.05

    /// A dash rather than a number the console never sent.
    static func formatDb(_ value: Double?) -> String {
        guard let value else { return "—" }

        let rounded = (value * 10).rounded() / 10
        return rounded > 0 ? String(format: "+%.1f", rounded) : String(format: "%.1f", rounded)
    }
}

/// Polarity, drawn as the console prints it: a circle with a stroke
/// through it. Left unfilled so the button can colour it with its own
/// state.
private struct PolaritySymbol: Shape {
    func path(in rect: CGRect) -> Path {
        let side = min(rect.width, rect.height)
        let center = CGPoint(x: rect.midX, y: rect.midY)
        let radius = side * 0.34375  // 8.25 of a 24pt box, as the console draws it

        return Path { path in
            path.addEllipse(
                in: CGRect(
                    x: center.x - radius, y: center.y - radius,
                    width: radius * 2, height: radius * 2
                )
            )

            let reach = side * 0.2417  // the stroke runs a little past the circle
            path.move(to: CGPoint(x: center.x - reach, y: center.y + reach))
            path.addLine(to: CGPoint(x: center.x + reach, y: center.y - reach))
        }
    }
}
