import UIKit

/// Exists for one thing: restricting rotation to portrait while the login
/// screen is up. Info.plist's UISupportedInterfaceOrientations lists
/// Landscape too, for the mixer screens - this narrows that down
/// per-screen, since iOS has no per-SwiftUI-view orientation lock and
/// only ever asks the app delegate.
///
/// `lockedToPortrait` is a static rather than something read off AppModel
/// directly because UIKit calls `application(_:supportedInterfaceOrientationsFor:)`
/// on its own schedule, outside SwiftUI's view update cycle, with no
/// reference to the running AppModel instance to hand it.
final class AppDelegate: NSObject, UIApplicationDelegate {
    static var lockedToPortrait = true {
        didSet {
            guard oldValue != lockedToPortrait else { return }
            // Flipping the flag alone doesn't make iOS re-ask this delegate -
            // this is what tells it to, so a phone already held sideways
            // rotates itself back to portrait the moment the login screen
            // reappears, rather than staying landscape until the next
            // physical turn.
            for scene in UIApplication.shared.connectedScenes {
                (scene as? UIWindowScene)?.windows.first?.rootViewController?
                    .setNeedsUpdateOfSupportedInterfaceOrientations()
            }
        }
    }

    func application(
        _ application: UIApplication,
        supportedInterfaceOrientationsFor window: UIWindow?
    ) -> UIInterfaceOrientationMask {
        Self.lockedToPortrait ? .portrait : .all
    }
}
