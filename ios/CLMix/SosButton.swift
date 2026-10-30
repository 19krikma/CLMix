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

    var body: some View {
        Button {
            model.sosOptionsShown = true
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
    }

    static let red = Color(red: 0xC6 / 255, green: 0x28 / 255, blue: 0x28 / 255)
}

/// The SOS choices, as a card centred over the mixer screen: each one a
/// full-width filled button, then CANCEL. Shown while
/// model.sosOptionsShown is set; tapping outside the card cancels.
struct SosOptions: View {
    @EnvironmentObject private var model: AppModel

    // Wire values, as services/remote_server.py's SOS_REASONS knows them.
    private static let reasons = [
        ("dead_battery", "DEAD BATTERY"),
        ("aux_not_working", "AUX NOT WORKING"),
        ("need_assistance", "NEED ASSISTANCE"),
    ]

    var body: some View {
        ZStack {
            if model.sosOptionsShown {
                Color.black.opacity(0.55)
                    .ignoresSafeArea()
                    .onTapGesture { model.sosOptionsShown = false }
                    .transition(.opacity)

                card
                    .transition(.scale(scale: 0.92).combined(with: .opacity))
            }
        }
        .animation(.easeOut(duration: 0.18), value: model.sosOptionsShown)
        // Left up when the screen goes, it would greet the next visit.
        .onDisappear { model.sosOptionsShown = false }
    }

    private var card: some View {
        VStack(spacing: 10) {
            Text("SOS - what do you need?")
                .font(.system(size: 20, weight: .bold))
                .foregroundStyle(Color.clmixOnSurface)
                .multilineTextAlignment(.center)
                .padding(.bottom, 10)

            ForEach(Self.reasons, id: \.0) { item in
                option(item.1, fill: SosButton.red, text: .white) {
                    model.sosOptionsShown = false
                    model.sendSos(reason: item.0)
                }
            }

            option("CANCEL", fill: .clmixMuteInactive, text: .clmixOnMuteInactive) {
                model.sosOptionsShown = false
            }
            .padding(.top, 8)
        }
        .padding(.horizontal, 24)
        .padding(.top, 24)
        .padding(.bottom, 20)
        .frame(maxWidth: 340)
        .background(Color.clmixSurface)
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .padding(.horizontal, 24)
    }

    private func option(_ title: String, fill: Color, text: Color,
                        action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 16, weight: .bold))
                .tracking(1)
                .foregroundStyle(text)
                .frame(maxWidth: .infinity, minHeight: 56)
                .background(fill)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
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
