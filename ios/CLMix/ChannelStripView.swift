import SwiftUI

struct ChannelStripView: View {
    @EnvironmentObject var model: AppModel
    let channel: ChannelState
    let fineMode: Bool
    // Alternates a subtle background so adjacent strips read as visually
    // separate columns instead of blurring together - mirrors Android's
    // ChannelAdapter tinting odd positions with R.color.surface_variant.
    var alternate: Bool = false

    @State private var showPanSheet = false

    var body: some View {
        VStack(spacing: 8) {
            Text(channel.name)
                .font(.system(size: 12, weight: .bold))
                .multilineTextAlignment(.center)
                .frame(height: 32)

            // Flexible height, not a fixed one - mirrors Android's
            // fader_row (layout_height="0dp", layout_weight="1"): the
            // channel name and Pan/Mute buttons above/below take only
            // what they need, and the fader stretches to fill whatever's
            // left, all the way to the bottom of the column.
            HStack(spacing: 0) {
                LevelRulerView()
                    .frame(width: 26)

                LevelFaderView(
                    db: channel.level ?? AuxTaper.bottomDb,
                    fineMode: fineMode,
                    onChange: { db in model.setLevel(channel: channel.channel, db: db) }
                )
                .frame(maxWidth: .infinity)

                // Beside the fader rather than against its ruler: this is
                // the console's own 0..-60 dB scale, not the fader's
                // -150..+10, and the two are not interchangeable.
                ChannelMeterView(channel: channel.channel, stereo: channel.stereo)
                    .frame(width: 10)
                    .padding(.leading, 4)
            }
            .frame(maxHeight: .infinity)

            // Both of these mirror a server-side truth rather than
            // deciding anything: the server rejects a pan write to a mono
            // bus and a mute from an account without the permission
            // regardless of what is drawn here. Hiding them just avoids
            // offering an action that could only come back as an error -
            // or worse, a pan control the console accepts and then does
            // nothing with.
            if model.panSupported {
                tonalButton(PanFormat.buttonLabel(channel.pan)) {
                    showPanSheet = true
                }
            }

            if model.muteAllowed {
                // A tap flips the button straight away rather than waiting
                // for the console's echo (see AppModel.setMute) - the
                // server stays the authority on what's actually muted.
                tonalButton(channel.muted ? "Muted" : "Mute", active: channel.muted) {
                    model.setMute(channel: channel.channel, muted: !channel.muted)
                }
            }
        }
        .padding(.horizontal, 6)
        .frame(width: 118)
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
        // Switching to a mono aux with the sheet already open would
        // otherwise leave a pan control on screen for a bus that has no
        // pan axis - mirrors Android's applyAuxWidth dismissing it.
        .onChange(of: model.panSupported) { _, supported in
            if !supported { showPanSheet = false }
        }
    }

    private func tonalButton(_ title: String, active: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 11))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
        }
        .foregroundStyle(active ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
        .background(active ? Color.clmixMuteActive : Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}
