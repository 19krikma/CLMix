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

            // Status indicator only, driven entirely by channel.muted from
            // the mixer - mute is never toggled from a phone, so this is a
            // plain Text (not a Button, which would stay VoiceOver-
            // actionable even with allowsHitTesting(false)), styled by
            // hand to match the borderedProminent look it replaces.
            Text(channel.muted ? "Muted" : "Mute")
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(channel.muted ? Color.red : Color.gray)
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .frame(width: 118)
        .sheet(isPresented: $showPanSheet) {
            PanSheetView(
                channelName: channel.name,
                pan: channel.pan ?? 0,
                onChange: { pan in model.setPan(channel: channel.channel, pan: pan) }
            )
            .presentationDetents([.height(280)])
        }
    }
}
