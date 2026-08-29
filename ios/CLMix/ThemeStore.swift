import SwiftUI

/// Mirrors Android's ThemeStore.kt: remembers whether the user has
/// overridden the phone's own light/dark setting from the mixer screen,
/// and puts that choice into effect app-wide via .preferredColorScheme.
///
/// Deliberately three states, not two: with nothing saved the app
/// follows the system setting exactly as it always has, and only stops
/// once the user has actually expressed a preference - so `isDarkMode`
/// is a Bool?, not a Bool.
@MainActor
final class ThemeStore: ObservableObject {
    static let shared = ThemeStore()

    private enum Key {
        static let darkMode = "clmix.darkMode"
    }

    @Published var isDarkMode: Bool? {
        didSet {
            if let isDarkMode {
                UserDefaults.standard.set(isDarkMode, forKey: Key.darkMode)
            } else {
                UserDefaults.standard.removeObject(forKey: Key.darkMode)
            }
        }
    }

    private init() {
        if UserDefaults.standard.object(forKey: Key.darkMode) != nil {
            isDarkMode = UserDefaults.standard.bool(forKey: Key.darkMode)
        } else {
            isDarkMode = nil
        }
    }

    /// Fed straight to `.preferredColorScheme` - nil there means "follow
    /// the system", same as nil here.
    var colorScheme: ColorScheme? {
        switch isDarkMode {
        case .some(true): return .dark
        case .some(false): return .light
        case .none: return nil
        }
    }
}
