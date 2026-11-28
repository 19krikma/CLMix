import SwiftUI

@main
struct CLMixApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    private var isChannelScreen: Bool {
        switch model.screen {
        case .mixer, .mixerControl: return true
        default: return false
        }
    }

    var body: some Scene {
        WindowGroup {
            NavigationStack {
                // A ZStack so the screen leaving and the one arriving can
                // be on screen together while they slide. Mixer Control
                // sits to the left of the aux screens; the slide only runs
                // when AppModel animates the change (AUX and Channels),
                // and every other move stays a cut.
                ZStack {
                    switch model.screen {
                    case .connect:
                        ConnectView()
                    case .auxList:
                        AuxListView()
                            .transition(.move(edge: .trailing))
                    case .mixer(let aux):
                        MixerView(aux: aux)
                            .transition(.move(edge: .trailing))
                    case .mixerControl:
                        MixerControlView()
                            .transition(.move(edge: .leading))
                    }
                }
                // The channel screens go full screen - mirrors Android's
                // enterFullScreen(). The clock strip goes outright; the
                // home indicator fades after a moment and comes back on
                // a touch near the bottom edge, which is as far as iOS
                // lets an app put it away.
                .statusBarHidden(isChannelScreen)
                .persistentSystemOverlays(isChannelScreen ? .hidden : .automatic)
            }
            // Over every screen, the first screen after login it sends the
            // app back to included - see AppModel.mixerDidReceiveSync.
            .overlay {
                if model.serverSyncing {
                    ServerSyncingCover()
                }
            }
            // An SOS for this sound tech to clear, over everything - see
            // SosAlertCover. Not on the login screen.
            .overlay {
                if let alert = model.sosAlerts.first, model.screen != .connect {
                    SosAlertCover(alert: alert, waiting: model.sosAlerts.count - 1)
                }
            }
            .environmentObject(model)
            // CLMix is dark at all times, whatever the phone is set to -
            // UIUserInterfaceStyle in Info.plist says the same to the
            // system. Above the switch rather than inside any one screen,
            // which is what carries it to the status bar's clock and icons
            // and to every sheet.
            .preferredColorScheme(.dark)
            // An iPad is always on its side - see
            // AppDelegate.holdLandscapeIfLargeScreen.
            .onAppear { AppDelegate.holdLandscapeIfLargeScreen(reason: "appeared") }
            .onChange(of: scenePhase) { _, phase in
                if phase == .active { AppDelegate.holdLandscapeIfLargeScreen(reason: "active") }
            }
        }
    }
}

/// "Server Syncing" while the desktop resyncs with the console, and
/// until the screen underneath has pulled its new info. Takes every
/// touch, so nothing underneath can be picked until it is done - the
/// server would refuse it anyway. Mirrors Android's ServerSync card.
struct ServerSyncingCover: View {
    @EnvironmentObject private var model: AppModel
    @State private var turning = false

    private var detail: String {
        if !model.syncLoading { return "Getting the latest from the console…" }
        return model.screen == .mixerControl ? "Loading Mixer Control…" : "Loading AUX list…"
    }

    var body: some View {
        ZStack {
            Color.black.opacity(0.6)
                .ignoresSafeArea()

            VStack(spacing: 0) {
                ZStack {
                    Circle()
                        .stroke(Color.clmixSurfaceVariant, lineWidth: 4)
                    Circle()
                        .trim(from: 0, to: 0.28)
                        .stroke(Color.clmixPrimary,
                                style: StrokeStyle(lineWidth: 4, lineCap: .round))
                        .rotationEffect(.degrees(turning ? 360 : 0))
                        .animation(.linear(duration: 1).repeatForever(autoreverses: false),
                                   value: turning)
                    // Still, inside the ring: one motion reads as smooth,
                    // two at different speeds as a stutter.
                    Image(systemName: "arrow.triangle.2.circlepath")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundStyle(Color.clmixPrimary)
                }
                .frame(width: 64, height: 64)

                Text("Server Syncing")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundStyle(Color.clmixOnSurface)
                    .padding(.top, 20)

                Text(detail)
                    .font(.system(size: 14))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                    .multilineTextAlignment(.center)
                    .padding(.top, 6)
            }
            .padding(.horizontal, 24)
            .padding(.top, 28)
            .padding(.bottom, 24)
            .frame(width: 280)
            .background(Color.clmixSurface)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .stroke(Color.clmixDivider, lineWidth: 1)
            )
        }
        .onAppear { turning = true }
        .transition(.opacity)
    }
}


/// An SOS a performer sent, on a sound tech's phone: what they need, who
/// and where from, and Clear. It stays, taking every touch, until it is
/// cleared - here, on another sound tech's phone, or with "Got it" on the
/// desktop. Several open at once are shown one at a time, oldest first.
/// Mirrors Android's SosAlerts.
struct SosAlertCover: View {
    @EnvironmentObject private var model: AppModel
    let alert: SosAlert
    let waiting: Int

    private static let red = Color(red: 0xC6 / 255, green: 0x28 / 255, blue: 0x28 / 255)

    private var details: String {
        let at = alert.time.formatted(date: .omitted, time: .shortened)
        let asked = alert.count > 1 ? "asked \(alert.count) times, last at \(at)" : "at \(at)"
        return [alert.whereFrom, asked].compactMap { $0 }.joined(separator: "  ·  ")
    }

    var body: some View {
        ZStack {
            Color.black.opacity(0.7)
                .ignoresSafeArea()

            VStack(spacing: 0) {
                Text("SOS")
                    .font(.system(size: 40, weight: .bold))
                Text(alert.reason)
                    .font(.system(size: 20, weight: .bold))
                    .multilineTextAlignment(.center)
                    .padding(.top, 4)
                Text("from \(alert.name)")
                    .font(.system(size: 15))
                    .padding(.top, 12)
                Text(details)
                    .font(.system(size: 13))
                    .opacity(0.85)
                    .multilineTextAlignment(.center)
                    .padding(.top, 4)
                if waiting > 0 {
                    Text("+\(waiting) more SOS waiting")
                        .font(.system(size: 12))
                        .opacity(0.85)
                        .padding(.top, 4)
                }

                Button {
                    model.clearSos(alert)
                } label: {
                    Text("Clear")
                        .font(.system(size: 16, weight: .bold))
                        .frame(maxWidth: .infinity)
                        .frame(height: 52)
                }
                .foregroundStyle(Self.red)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.top, 18)
            }
            .foregroundStyle(Color.white)
            .padding(.horizontal, 24)
            .padding(.top, 22)
            .padding(.bottom, 20)
            .frame(width: 300)
            .background(Self.red)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        }
        .transition(.opacity)
    }
}
