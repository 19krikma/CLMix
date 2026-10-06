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
            // One choice for the whole app, the connect screen included:
            // its wallpaper has a day cut now (see LoginBackgroundView),
            // so nothing is left that has to be dark whatever the theme
            // says. Above the switch rather than inside any one screen,
            // which is what carries the choice to the status bar's clock
            // and icons over the top of the artwork - a
            // .preferredColorScheme set deeper in the tree loses to this
            // one.
            .preferredColorScheme(themeStore.colorScheme)
        }
    }
}
