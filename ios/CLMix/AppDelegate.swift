import UIKit

/// Exists for one thing: restricting rotation per screen - portrait while
/// the login screen is up, landscape while a channel's EQ is open. Info.plist's UISupportedInterfaceOrientations lists
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

    /// Set while the EQ screen is up: its frequency axis wants the width.
    /// Unlike the portrait lock this one also asks the scene to turn
    /// straight away, so the screen opens on its side rather than waiting
    /// for the phone to be turned. Mirrors Android's EqActivity being
    /// fixed to sensorLandscape.
    static var lockedToLandscape = false {
        didSet {
            guard oldValue != lockedToLandscape else { return }

            for scene in UIApplication.shared.connectedScenes {
                guard let windowScene = scene as? UIWindowScene else { continue }
                windowScene.windows.first?.rootViewController?
                    .setNeedsUpdateOfSupportedInterfaceOrientations()

                if lockedToLandscape {
                    windowScene.requestGeometryUpdate(
                        .iOS(interfaceOrientations: .landscape)
                    ) { _ in }
                }
            }
        }
    }

    func application(
        _ application: UIApplication,
        supportedInterfaceOrientationsFor window: UIWindow?
    ) -> UIInterfaceOrientationMask {
        if Self.lockedToLandscape { return .landscape }
        return Self.lockedToPortrait ? .portrait : .all
    }
}
