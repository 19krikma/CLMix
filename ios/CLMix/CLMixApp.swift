import SwiftUI

@main
struct CLMixApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()
    @StateObject private var themeStore = ThemeStore.shared

    private var isChannelScreen: Bool {
        switch model.screen {
        case .mixer, .mixerControl: return true
        default: return false
        }
    }

    var body: some Scene {
        WindowGroup {
            NavigationStack {
                Group {
                    switch model.screen {
                    case .connect:
                        ConnectView()
                    case .controlChoice:
                        ControlChoiceView()
                    case .auxList:
                        AuxListView()
                    case .mixer(let aux):
                        MixerView(aux: aux)
                    case .mixerControl:
                        MixerControlView()
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
            .environmentObject(model)
            .environmentObject(themeStore)
            // The connect screen is dark whatever the theme says: it is
            // drawn over black artwork with no light cut of it, so the
            // window goes dark with it (ConnectView pins its own palette
            // to match). Doing it here rather than inside ConnectView is
            // what makes the status bar's clock and icons white over the
            // picture - a .preferredColorScheme set deeper in the tree
            // loses to this one, which sits above the whole switch.
            .preferredColorScheme(model.screen == .connect ? .dark : themeStore.colorScheme)
        }
    }
}
