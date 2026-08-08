import SwiftUI

struct ChannelStripView: View {
    @EnvironmentObject var model: AppModel
    let channel: ChannelState
    let fineMode: Bool

    @State private var showPanSheet = false

    var body: some View {
        VStack(spacing: 8) {
            Text(channel.name)
                .font(.caption)
                .bold()
                .multilineTextAlignment(.center)
                .frame(height: 32)

            HStack(spacing: 0) {
                LevelRulerView()
                    .frame(width: 26, height: 220)

                LevelFaderView(
                    db: channel.level ?? AuxTaper.bottomDb,
                    fineMode: fineMode,
                    onChange: { db in model.setLevel(channel: channel.channel, db: db) }
                )
                .frame(width: 44, height: 220)
            }

            Button(PanFormat.buttonLabel(channel.pan)) {
                showPanSheet = true
            }
            .buttonStyle(.bordered)
            .frame(maxWidth: .infinity)

            Button(channel.muted ? "Muted" : "Mute") {
                model.setMute(channel: channel.channel, muted: !channel.muted)
            }
            .buttonStyle(.borderedProminent)
            .tint(channel.muted ? .red : .gray)
            .frame(maxWidth: .infinity)
        }
        .frame(width: 118)
        .sheet(isPresented: $showPanSheet) {
            PanSheetView(
                channelName: channel.name,
                initialPan: channel.pan ?? 0,
                onChange: { pan in model.setPan(channel: channel.channel, pan: pan) }
            )
            .presentationDetents([.height(280)])
        }
    }
}
