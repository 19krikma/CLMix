import SwiftUI

@main
struct CLMixApp: App {
    @StateObject private var model = AppModel()
    @StateObject private var themeStore = ThemeStore.shared

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
            }
            .environmentObject(model)
            .environmentObject(themeStore)
            .preferredColorScheme(themeStore.colorScheme)
        }
    }
}
