import SwiftUI
import UIKit

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

    /// Which of the two the app is actually in, for the connect
    /// screen's sun/moon control to report. With nothing saved there is
    /// no choice to read, so this falls back to what the phone itself is
    /// set to and the control starts out agreeing with what the user
    /// will see once they are past login.
    var isDarkEffective: Bool {
        if let isDarkMode { return isDarkMode }
        return Self.systemIsDark
    }

    /// Deliberately not `UITraitCollection.current`: that resolves
    /// against whatever is being evaluated at the time, which the app's
    /// own `.preferredColorScheme` can have overridden, where a scene's
    /// traits carry the phone's setting whatever the app has asked for.
    /// This is only read when nothing has been saved - so in practice
    /// the two agree - but the scene is the one that answers the
    /// question actually being asked. Unspecified counts as dark, which
    /// is the app's own default look.
    private static var systemIsDark: Bool {
        let scene = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first
        return scene?.traitCollection.userInterfaceStyle != .light
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
