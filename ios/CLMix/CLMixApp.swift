import SwiftUI

@main
struct CLMixApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

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
            .environmentObject(model)
            // CLMix is dark at all times, whatever the phone is set to -
            // UIUserInterfaceStyle in Info.plist says the same to the
            // system. Above the switch rather than inside any one screen,
            // which is what carries it to the status bar's clock and icons
            // and to every sheet.
            .preferredColorScheme(.dark)
        }
    }
}

/// "Server Syncing" while the desktop resyncs with the console. Takes
/// every touch, so nothing underneath can be picked until it is done -
/// the server would refuse it anyway. Mirrors Android's ServerSync dialog.
struct ServerSyncingCover: View {
    var body: some View {
        ZStack {
            Color.black.opacity(0.45)
                .ignoresSafeArea()

            HStack(spacing: 16) {
                ProgressView()
                    .controlSize(.large)
                Text("Server Syncing")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(Color.clmixOnSurface)
            }
            .padding(24)
            .background(Color.clmixSurface)
            .clipShape(RoundedRectangle(cornerRadius: 16))
        }
        .transition(.opacity)
    }
}
