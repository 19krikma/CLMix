import SwiftUI

/// A channel's input stage: its name, and its two input slots side by
/// side - Main and ALT - each with its own 48V and head-amp gain, plus the
/// digital trim behind them, each on a dial.
///
/// Opened from the channel number above the name on the Full Mixer
/// Control strips. All of it is console-wide - a head amp feeds FOH,
/// every monitor and the recording at once - which is why it lives behind
/// a sheet of its own rather than on the strip, and why the dials have to
/// be held to move (see DialView).
///
/// The Main / ALT buttons pick which slot feeds the channel. The ALT
/// column stays disabled until the console reports an alt route to switch
/// to (ChannelState.altAvailable). Trim sits after that switch on the
/// console, so there is only one: the Trim dial in each column is the
/// same value, and turning either moves both.
///
/// Mirrors Android's ChannelInputBottomSheet.kt.
struct ChannelInputSheet: View {
    /// Live: the mixer-control screen passes the channel fresh on every
    /// push, so the sheet keeps up with the console while it is open.
    let channel: ChannelState
    let onGainChanged: (Int, Double) -> Void
    let onTrimChanged: (Int, Double) -> Void
    let onPhantomChanged: (Int, Bool) -> Void
    let onAltGainChanged: (Int, Double) -> Void
    let onAltPhantomChanged: (Int, Bool) -> Void
    let onAltInChanged: (Int, Bool) -> Void
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
    @State private var altGainShown: Double?
    @State private var trimShown: Double?

    // When each value was last turned by hand. A push arriving right
    // after a turn still carries the console's pre-turn value (it has to
    // travel to the desk and back), and writing that into the dial would
    // drag it backwards under the finger - so pushes are ignored briefly
    // after a turn, the same bargain the channel strips strike for their
    // faders. One per console value, so the two Trim dials share theirs.
    @State private var gainTouchedAt = Date.distantPast
    @State private var altGainTouchedAt = Date.distantPast
    @State private var trimTouchedAt = Date.distantPast

    // When each value last sent a write. A dial turns at the display's
    // own rate, and sending every one of those frames would put ~60
    // writes a second per dial on the console for a gesture the operator
    // experiences as one move. Held to writeInterval while turning; the
    // value it settles on is always sent, so the console never ends up on
    // a rounded-off intermediate.
    @State private var gainSentAt = Date.distantPast
    @State private var altGainSentAt = Date.distantPast
    @State private var trimSentAt = Date.distantPast

    // The toggles - 48V on each slot, which slot is live, and polarity -
    // flipped on tap rather than waiting for the console's echo, the same
    // bargain the strip's Mute button strikes. Pushes are ignored until
    // they agree or flagConfirm passes, so a frame already in flight
    // cannot flip one back.
    @State private var phantom = Optimistic(false)
    @State private var altPhantom = Optimistic(false)
    @State private var altIn = Optimistic(false)

    // The polarity state being shown, as the console's own value rather
    // than a flag: 0 normal, 1...3 inverted (see ChannelState.phase).
    @State private var phase = Optimistic(PhaseState.normal)

    // The inverted state to go back to when polarity is switched on again.
    // A stereo channel has three of them and this app cannot tell them
    // apart, so it remembers the one the desk reported instead of assuming
    // PhaseState.inverted - otherwise tapping polarity off and on would
    // quietly move which leg is inverted.
    @State private var phaseLastInverted = PhaseState.inverted

    // A rename takes a moment to reach the console and come back. Until
    // it does, pushes still carry the old name, and writing that into the
    // box would undo what was just typed in front of the user.
    @State private var name = Optimistic("")

    // What the name box holds - the name, or what is being typed over it.
    @State private var draftName = ""
    @FocusState private var editingName: Bool

    var body: some View {
        // Scrolling, because the sheet does not always get the height
        // it asks for: in landscape the phone reports a compact
        // height, which makes every sheet full-screen and ignores the
        // detent below outright - and this content is taller than the
        // screen is in that orientation, so the Trim dials at the bottom
        // would simply be cut off. .scrollBounceBehavior(.basedOnSize)
        // keeps it feeling like a fixed panel wherever it does fit,
        // which is every portrait case.
        ScrollView {
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

                HStack(alignment: .top, spacing: 16) {
                    mainColumn
                    altColumn
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 10)
            .padding(.bottom, 28)
            .frame(maxWidth: .infinity)
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollDismissesKeyboard(.interactively)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clmixSurface)
        .onAppear(perform: seed)
        .onChange(of: channel) { _, state in fold(state) }
        .onChange(of: editingName) { _, editing in
            if !editing { commitRename() }
        }
        // Closing the sheet mid-edit sends what was typed - it is what
        // the operator meant.
        .onDisappear {
            if editingName { commitRename() }
        }
    }

    // MARK: - Name

    /// The name reads as a value the console holds, like gain and trim
    /// below, so it wears the same readout box - and it is edited right
    /// there: tap it, clear it (the x appears while typing), type the new
    /// name, Done.
    private var nameRow: some View {
        HStack(spacing: 12) {
            TextField("Channel name", text: $draftName)
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(Color.clmixReadoutText)
                .multilineTextAlignment(.center)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                .submitLabel(.done)
                .focused($editingName)
                .onSubmit { editingName = false }
                .onChange(of: draftName) { _, typed in
                    if typed.count > Self.maxNameLength {
                        draftName = String(typed.prefix(Self.maxNameLength))
                    }
                }
                .padding(.vertical, 12)
                .padding(.horizontal, 30)
                .background { readoutBox }
                .overlay(alignment: .trailing) {
                    if editingName && !draftName.isEmpty {
                        Button {
                            draftName = ""
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(Color.clmixOnSurfaceVariant)
                                .padding(.trailing, 8)
                        }
                        .accessibilityLabel("Clear name")
                    }
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
        let inverted = phase.shown != PhaseState.normal

        return Button {
            let target = inverted ? PhaseState.normal : phaseLastInverted
            phase.set(target)
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

    /// An empty box is not a name - it puts back the one the desk has.
    private func commitRename() {
        let typed = draftName.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !typed.isEmpty, typed != name.shown else {
            draftName = name.shown
            return
        }

        let sent = String(typed.prefix(Self.maxNameLength))
        name.set(sent)
        draftName = sent
        onNameChanged(channel.channel, sent)
    }

    // MARK: - Main and ALT

    private var mainColumn: some View {
        inputColumn(
            title: "Main",
            live: !altIn.shown,
            onSelect: { selectSource(alt: false) },
            phantomOn: phantom.shown,
            onPhantom: {
                phantom.set(!phantom.shown)
                onPhantomChanged(channel.channel, phantom.shown)
            },
            gain: gainShown,
            onGain: { value, force in
                gainShown = value
                throttle(value, force, touched: &gainTouchedAt, sent: &gainSentAt) {
                    onGainChanged(channel.channel, $0)
                }
            }
        )
    }

    /// Greyed out and locked while the console reports no alt route.
    private var altColumn: some View {
        inputColumn(
            title: "ALT",
            live: altIn.shown,
            onSelect: { selectSource(alt: true) },
            phantomOn: altPhantom.shown,
            onPhantom: {
                altPhantom.set(!altPhantom.shown)
                onAltPhantomChanged(channel.channel, altPhantom.shown)
            },
            gain: altGainShown,
            onGain: { value, force in
                altGainShown = value
                throttle(value, force, touched: &altGainTouchedAt, sent: &altGainSentAt) {
                    onAltGainChanged(channel.channel, $0)
                }
            }
        )
        .disabled(!channel.altAvailable)
        .allowsHitTesting(channel.altAvailable)
        .opacity(channel.altAvailable ? 1 : 0.4)
    }

    /// One input slot: the button that makes it live, its 48V as a small
    /// square, its gain, and the shared trim.
    private func inputColumn(
        title: String,
        live: Bool,
        onSelect: @escaping () -> Void,
        phantomOn: Bool,
        onPhantom: @escaping () -> Void,
        gain: Double?,
        onGain: @escaping (Double, Bool) -> Void
    ) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onSelect) {
                Text(title)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            }
            .foregroundStyle(live ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
            .background(live ? Color.clmixPrimary : Color.clmixMuteInactive)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

            // 48V on reads as the console's own warning colour, not the
            // app's accent: it is the one control on this sheet that can
            // damage a source (ribbon mics especially), so it should look
            // like a live state rather than a selected option.
            Button(action: onPhantom) {
                Text("48V")
                    .font(.system(size: 12, weight: .semibold))
                    .frame(width: 44, height: 44)
            }
            .foregroundStyle(phantomOn ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
            .background(phantomOn ? Color.clmixMuteActive : Color.clmixMuteInactive)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .accessibilityLabel(phantomOn ? "48V on" : "48V off")
            .padding(.top, 6)
            .padding(.bottom, 12)

            dialRow(label: "Gain", value: gain, range: gainRange, onChanged: onGain)
                .padding(.bottom, 12)

            dialRow(
                label: "Trim",
                value: trimShown,
                range: trimRange,
                onChanged: { value, force in
                    trimShown = value
                    throttle(value, force, touched: &trimTouchedAt, sent: &trimSentAt) {
                        onTrimChanged(channel.channel, $0)
                    }
                }
            )
        }
        .frame(maxWidth: .infinity)
    }

    private func selectSource(alt: Bool) {
        guard alt != altIn.shown else { return }
        guard !alt || channel.altAvailable else { return }

        altIn.set(alt)
        onAltInChanged(channel.channel, alt)
    }

    // MARK: - Dials

    /// Each dial's label sits directly over its readout, so the pair
    /// reads as one control.
    private func dialRow(
        label: String,
        value: Double?,
        range: ClosedRange<Double>,
        onChanged: @escaping (Double, Bool) -> Void
    ) -> some View {
        HStack(spacing: 8) {
            DialView(
                value: value ?? range.lowerBound,
                range: range,
                hasValue: value != nil,
                onValueChanged: { onChanged($0, false) },
                // Always sent, throttling or not: this is the value the
                // operator actually chose.
                onTurnFinished: { onChanged($0, true) }
            )

            VStack(spacing: 3) {
                Text(label)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)

                Text(Self.formatDb(value))
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Color.clmixReadoutText)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .background { readoutBox }
            }
        }
    }

    /// Notes the turn, and sends it unless one went out within
    /// writeInterval - the settled value always goes.
    private func throttle(
        _ value: Double, _ force: Bool,
        touched: inout Date, sent: inout Date,
        send: (Double) -> Void
    ) {
        touched = Date()

        if force || touched.timeIntervalSince(sent) >= Self.writeInterval {
            sent = touched
            send(value)
        }
    }

    // MARK: - Folding in pushes

    private func seed() {
        gainShown = channel.gain
        altGainShown = channel.altGain
        trimShown = channel.trim
        phantom = Optimistic(channel.phantom)
        altPhantom = Optimistic(channel.altPhantom)
        altIn = Optimistic(channel.altIn)
        phase = Optimistic(channel.phase)
        if channel.phase != PhaseState.normal { phaseLastInverted = channel.phase }
        name = Optimistic(channel.name)
        draftName = channel.name
    }

    /// Folds in a push from the server, unless that control has just been
    /// worked by hand.
    private func fold(_ state: ChannelState) {
        let now = Date()

        if name.reconcile(state.name, confirm: Self.nameConfirm), !editingName {
            draftName = name.shown
        }

        if let gain = state.gain, now.timeIntervalSince(gainTouchedAt) > Self.settle,
           differs(gainShown, gain) {
            gainShown = gain
        }

        if let altGain = state.altGain, now.timeIntervalSince(altGainTouchedAt) > Self.settle,
           differs(altGainShown, altGain) {
            altGainShown = altGain
        }

        if let trim = state.trim, now.timeIntervalSince(trimTouchedAt) > Self.settle,
           differs(trimShown, trim) {
            trimShown = trim
        }

        phantom.reconcile(state.phantom, confirm: Self.flagConfirm)
        altPhantom.reconcile(state.altPhantom, confirm: Self.flagConfirm)
        altIn.reconcile(state.altIn, confirm: Self.flagConfirm)
        phase.reconcile(state.phase, confirm: Self.flagConfirm)

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

    // The dials' ranges are no longer constants here: each sweeps its own
    // parameter's range, which the server states at login and
    // HeadAmpRange defaults for. They used to share one span, the union
    // -40...+60, which gave gain 20 dB of travel the desk only ever clamps
    // away.

    // How long after a turn to keep ignoring pushes for that dial.
    private static let settle: TimeInterval = 0.7

    // How long a tapped 48V, polarity or Main/ALT button holds its own
    // state before deferring to the console again.
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

/// A value changed on tap rather than waiting for the console's echo.
/// Pushes are ignored until they agree or the confirm window passes, so a
/// frame already in flight cannot flip it back; after that the console's
/// state wins. Mirrors ChannelInputBottomSheet.Optimistic on Android.
private struct Optimistic<Value: Equatable> {
    private(set) var shown: Value
    private var expected: Value?
    private var sentAt = Date.distantPast

    init(_ value: Value) {
        shown = value
    }

    mutating func set(_ value: Value) {
        expected = value
        sentAt = Date()
        shown = value
    }

    /// Takes a pushed value; true if what is shown changed.
    @discardableResult
    mutating func reconcile(_ incoming: Value, confirm: TimeInterval) -> Bool {
        if let expected,
           incoming == expected || Date().timeIntervalSince(sentAt) > confirm {
            self.expected = nil
        }

        guard expected == nil, incoming != shown else { return false }

        shown = incoming
        return true
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
