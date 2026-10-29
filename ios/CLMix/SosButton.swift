import SwiftUI

/// The SOS button on the AUX mixer screen, beside the menu button - in the
/// top bar, and in the landscape sidebar. It offers DEAD BATTERY, AUX NOT
/// WORKING, NEED ASSISTANCE or CANCEL; the first three go to the server,
/// which pops an SOS window up on the sound engineer's desktop saying what
/// is needed and who asked. Mirrors Android's Sos.kt.
struct SosButton: View {
    @EnvironmentObject private var model: AppModel

    /// The landscape sidebar's narrower one.
    var compact = false

    // Wire values, as services/remote_server.py's SOS_REASONS knows them.
    private static let reasons = [
        ("dead_battery", "DEAD BATTERY"),
        ("aux_not_working", "AUX NOT WORKING"),
        ("need_assistance", "NEED ASSISTANCE"),
    ]

    @State private var showOptions = false

    var body: some View {
        Button {
            showOptions = true
        } label: {
            Text("SOS")
                .font(.system(size: compact ? 12 : 13, weight: .bold))
                .tracking(compact ? 0.6 : 1.2)
                .foregroundStyle(.white)
                .padding(.horizontal, compact ? 10 : 14)
                .frame(height: compact ? 32 : 36)
                .background(Self.red)
                .clipShape(Capsule())
        }
        .accessibilityLabel("SOS - call the sound engineer")
        .confirmationDialog("SOS - what do you need?", isPresented: $showOptions,
                            titleVisibility: .visible) {
            ForEach(Self.reasons, id: \.0) { item in
                Button(item.1, role: .destructive) { model.sendSos(reason: item.0) }
            }
            Button("CANCEL", role: .cancel) {}
        }
    }

    static let red = Color(red: 0xC6 / 255, green: 0x28 / 255, blue: 0x28 / 255)
}

/// What became of the last SOS ("SOS sent - ..."), for a few seconds near
/// the top of the mixer screen.
struct SosNotice: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack {
            if let notice = model.sosNotice {
                Text(notice)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color.clmixOnSurface)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(Color.clmixSurfaceVariant)
                    .clipShape(Capsule())
                    .padding(.top, 56)
                    .padding(.horizontal, 24)
                    .transition(.opacity)
            }
            Spacer()
        }
        .allowsHitTesting(false)
        .animation(.easeInOut(duration: 0.2), value: model.sosNotice)
    }
}
