import SwiftUI

/// One of a channel's insert points, A or B (DiGiCo's Send A / Send B) -
/// whichever INS button on a Full Mixer Control strip was tapped. Each
/// has its own sheet: A and B are separate settings on the console, so
/// they are kept apart here too.
///
/// The insert's in/out beside the channel name, then the return's 48V,
/// pad and head-amp gain on a dial with a box that can be typed into - the same
/// furniture, and the same rules, as ChannelInputSheet: toggles flip on
/// tap ahead of the console's echo, dial writes are throttled while
/// turning, and pushes are ignored for a moment after a turn.
///
/// Mirrors Android's InsertBottomSheet.
struct InsertSheet: View {
    /// Live: passed fresh on every push, so the sheet keeps up.
    let channel: ChannelState
    /// "A" or "B" - the insert this sheet is for.
    let slot: String
    /// (channel, "A"/"B", "in"/"phantom"/"pad"/"gain", value)
    let onChanged: (Int, String, String, Any) -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                Capsule()
                    .fill(Color.clmixTrackBackground)
                    .frame(width: 40, height: 4)
                    .padding(.bottom, 18)

                Text("Channel \(channel.channel)")
                    .font(.system(size: 13))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                    .padding(.bottom, 6)

                InsertControls(slot: slot, name: channel.name,
                               insert: slot == "A" ? channel.insertA : channel.insertB) {
                    onChanged(channel.channel, slot, $0, $1)
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 10)
            .padding(.bottom, 28)
        }
        .scrollBounceBehavior(.basedOnSize)
        .scrollDismissesKeyboard(.interactively)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clmixSurface)
    }
}

/// The insert's controls in two rows: the channel's name with the
/// insert's in/out button beside it - as the input sheet pairs the name
/// with polarity - and then the return's head amp in signal order: 48V,
/// pad, gain.
private struct InsertControls: View {
    let slot: String
    let name: String
    let insert: InsertState?
    let onChanged: (String, Any) -> Void

    private static let gainRange = -20.0...60.0
    private static let settle: TimeInterval = 0.7
    private static let flagConfirm: TimeInterval = 2
    private static let writeInterval: TimeInterval = 0.05

    @State private var inOn = Optimistic(false)
    @State private var phantom = Optimistic(false)
    @State private var pad = Optimistic(false)
    @State private var gainShown: Double?
    @State private var gainTouchedAt = Date.distantPast
    @State private var gainSentAt = Date.distantPast

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                Text(name)
                    .font(.system(size: 16, weight: .bold))
                    .foregroundStyle(Color.clmixReadoutText)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(readoutBox)

                // Puts the insert in or out: lit in the accent while it is in.
                Button {
                    inOn.set(!inOn.shown)
                    onChanged("in", inOn.shown)
                } label: {
                    Text("Insert \(slot)")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .foregroundStyle(inOn.shown ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
                .background(inOn.shown ? Color.clmixPrimary : Color.clmixMuteInactive)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .frame(width: 116)
                .accessibilityLabel("Insert \(slot) \(inOn.shown ? "in" : "out")")
            }
            .frame(height: 52)
            .padding(.bottom, 18)

            HStack(spacing: 8) {
                // 48V in the console's warning colour, as on the input sheet.
                square("48V", on: phantom.shown, fill: Color.clmixMuteActive) {
                    phantom.set(!phantom.shown)
                    onChanged("phantom", phantom.shown)
                }
                square("PAD", on: pad.shown, fill: Color.clmixPrimary) {
                    pad.set(!pad.shown)
                    onChanged("pad", pad.shown)
                }

                DialView(
                    value: gainShown ?? Self.gainRange.lowerBound,
                    range: Self.gainRange,
                    hasValue: gainShown != nil,
                    onValueChanged: { turned($0, force: false) },
                    onTurnFinished: { turned($0, force: true) },
                    // A head amp: the same quarter-speed turn as the input sheet's.
                    rate: 0.25
                )
                .padding(.leading, 8)

                VStack(spacing: 3) {
                    Text("Gain")
                        .font(.system(size: 12))
                        .foregroundStyle(Color.clmixOnSurfaceVariant)
                    ValueField(value: gainShown, range: Self.gainRange, enabled: true) {
                        turned($0, force: true)
                    }
                    .background(readoutBox)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .frame(maxWidth: .infinity)
        .onAppear(perform: seed)
        .onChange(of: insert) { _, state in fold(state) }
    }

    private var readoutBox: some View {
        RoundedRectangle(cornerRadius: 6, style: .continuous)
            .fill(Color.clmixReadoutFill)
            .overlay(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .stroke(Color.clmixReadoutStroke, lineWidth: 1)
            )
    }

    private func square(_ title: String, on: Bool, fill: Color,
                        action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .frame(width: 44, height: 44)
        }
        .foregroundStyle(on ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
        .background(on ? fill : Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityLabel("\(title) \(on ? "on" : "off")")
    }

    private func turned(_ value: Double, force: Bool) {
        gainShown = value
        gainTouchedAt = Date()

        if force || gainTouchedAt.timeIntervalSince(gainSentAt) >= Self.writeInterval {
            gainSentAt = gainTouchedAt
            onChanged("gain", value)
        }
    }

    private func seed() {
        inOn = Optimistic(insert?.inOn ?? false)
        phantom = Optimistic(insert?.phantom ?? false)
        pad = Optimistic(insert?.pad ?? false)
        gainShown = insert?.gain
    }

    private func fold(_ state: InsertState?) {
        guard let state else { return }

        if let value = state.inOn { inOn.reconcile(value, confirm: Self.flagConfirm) }
        if let value = state.phantom { phantom.reconcile(value, confirm: Self.flagConfirm) }
        if let value = state.pad { pad.reconcile(value, confirm: Self.flagConfirm) }

        if let gain = state.gain, Date().timeIntervalSince(gainTouchedAt) > Self.settle {
            if gainShown == nil || abs(gainShown! - gain) >= 0.05 { gainShown = gain }
        }
    }
}
