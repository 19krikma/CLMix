import os
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
            // An iPad's answer never changes (landscape, always), and
            // asking iPadOS to re-read it lets it turn the screen to
            // whichever side the iPad is tipped to - the turn the iPad's
            // orientation lock is there to stop.
            guard !LargeScreen.isActive else { return }
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
            // An iPad is on its side already, held there - see
            // lockedToPortrait for why asking again would undo that.
            guard !LargeScreen.isActive else { return }

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

    static let orientationLog = Logger(subsystem: "com.clmix.CLMix", category: "orientation")

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        if LargeScreen.isActive {
            // The lock is asked for again on every turn of the iPad, not
            // only when the app comes to the front: an app opened upright
            // cannot be locked until it fills the screen (see
            // installOrientationLock), and the turn that makes it fill
            // the screen is the moment to ask.
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
            NotificationCenter.default.addObserver(
                forName: UIDevice.orientationDidChangeNotification,
                object: nil, queue: .main
            ) { _ in
                MainActor.assumeIsolated { Self.holdLandscapeIfLargeScreen(reason: "device turned") }
            }
        }
        return true
    }

    /// Puts an iPad's screen on its side and keeps it there, the way a
    /// game does - see installOrientationLock for what holds it and the
    /// one case it cannot. Called when the app comes to the front
    /// (CLMixApp) and whenever the iPad is turned.
    @MainActor
    static func holdLandscapeIfLargeScreen(reason: String) {
        guard LargeScreen.isActive else { return }
        for scene in UIApplication.shared.connectedScenes {
            guard let windowScene = scene as? UIWindowScene,
                  let root = windowScene.windows.first?.rootViewController
            else { continue }

            installOrientationLock(on: root)
            logOrientation(reason, windowScene)

            // The side the iPad is tipped to, if it is on its side at all.
            // Upright, upside down, flat on a desk: none of those is a side
            // to follow, and the lock holds whichever side is showing.
            let current = windowScene.effectiveGeometry.interfaceOrientation
            let target = matchingLandscape(for: UIDevice.current.orientation)

            if let target, current != target {
                // Turned over to the other side: follow it, the way a game
                // does. The lock pins the screen as it stands, so it is let
                // go for the length of the turn and taken back after.
                followingTurn = true
                if #available(iOS 26.0, *) {
                    root.setNeedsUpdateOfPrefersInterfaceOrientationLocked()
                }
                windowScene.requestGeometryUpdate(
                    .iOS(interfaceOrientations: mask(for: target))
                ) { error in
                    orientationLog.error("turn refused: \(error.localizedDescription, privacy: .public)")
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + turnDuration) {
                    followingTurn = false
                }
            } else if !current.isLandscape {
                // Somehow upright with the iPad not on its side either:
                // either landscape will do. Never asked of a screen already
                // on its side - "either" would let iPadOS flip it to
                // whichever side it liked.
                windowScene.requestGeometryUpdate(
                    .iOS(interfaceOrientations: .landscape)
                ) { error in
                    orientationLog.error("turn refused: \(error.localizedDescription, privacy: .public)")
                }
            }

            // A turn is animated and has no completion of its own, so the
            // lock is asked for again once it has had time to land. Asking
            // early does no harm: the override says no until the screen
            // really is on its side, and while a turn is being followed.
            for delay in [0.0, 0.4, turnDuration + 0.05, 1.0] {
                DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                    if #available(iOS 26.0, *) {
                        root.setNeedsUpdateOfPrefersInterfaceOrientationLocked()
                    }
                    if delay == 1.0 { logOrientation("\(reason) +1s", windowScene) }
                }
            }
        }
    }

    /// Set while the screen is turning over to follow the iPad to its
    /// other side; the lock is let go for that long. See
    /// holdLandscapeIfLargeScreen.
    private static var followingTurn = false

    /// How long the lock stays off for a turn: a rotation's animation and
    /// a little over, short enough that tipping the iPad upright straight
    /// after cannot catch the screen unlocked.
    private static let turnDuration: TimeInterval = 0.6

    /// The screen orientation that matches the iPad lying on that side -
    /// crossed over, because the device names the side the home edge is
    /// on and the interface names the side the top of the screen is on.
    private static func matchingLandscape(
        for device: UIDeviceOrientation
    ) -> UIInterfaceOrientation? {
        switch device {
        case .landscapeLeft: return .landscapeRight
        case .landscapeRight: return .landscapeLeft
        default: return nil
        }
    }

    private static func mask(for orientation: UIInterfaceOrientation) -> UIInterfaceOrientationMask {
        orientation == .landscapeLeft ? .landscapeLeft : .landscapeRight
    }

    /// One line per check, readable from a test machine with
    /// `log stream --predicate 'subsystem == "com.clmix.CLMix"'` - the
    /// only way to tell a held lock from one that was never granted.
    private static func logOrientation(_ reason: String, _ scene: UIWindowScene) {
        let interface = scene.effectiveGeometry.interfaceOrientation.rawValue
        let device = UIDevice.current.orientation.rawValue
        if #available(iOS 26.0, *) {
            let locked = scene.effectiveGeometry.isInterfaceOrientationLocked
            orientationLog.info("\(reason, privacy: .public): interface=\(interface) device=\(device) locked=\(locked)")
        } else {
            orientationLog.info("\(reason, privacy: .public): interface=\(interface) device=\(device)")
        }
    }

    /// Keeps an iPad on its side however it is held, the way a game does.
    /// From iPadOS 26 an app's supported orientations alone no longer do
    /// that - UIRequiresFullScreen is deprecated, and turning the iPad
    /// upright shrank CLMix's landscape screen into the middle of the
    /// upright one, with bars above and below. What a full-screen game
    /// uses instead is `prefersInterfaceOrientationLocked` on the root
    /// view controller, which pins the screen in whichever orientation
    /// it is in - so the override only says yes once that is landscape.
    ///
    /// iPadOS only grants the lock while the app fills the screen, which
    /// a landscape-only app on an upright iPad does not: opened upright,
    /// CLMix starts letterboxed, and there is no API to avoid that. The
    /// first time the iPad is turned on its side the lock takes hold, and
    /// from then on turning it upright again leaves CLMix full screen and
    /// sideways. Turning it over onto its other side still turns CLMix
    /// with it - holdLandscapeIfLargeScreen lets the lock go for that one
    /// turn. Checked in the iPad simulator on iPadOS 26.
    ///
    /// The root view controller is SwiftUI's own hosting controller,
    /// which there is no subclassing from an `App`, so the override is
    /// added to its class at runtime instead. Added, not swapped:
    /// `class_addMethod` only succeeds on a class that does not already
    /// implement the getter itself, so nothing of SwiftUI's is replaced,
    /// and UIViewController's own default (false) is left alone for
    /// every other controller.
    private static func installOrientationLock(on root: UIViewController) {
        guard #available(iOS 26.0, *) else { return }

        let selector = #selector(getter: UIViewController.prefersInterfaceOrientationLocked)
        let locked: @convention(block) (AnyObject) -> Bool = { controller in
            guard !followingTurn,
                  let scene = (controller as? UIViewController)?.view.window?.windowScene
            else { return false }
            return scene.effectiveGeometry.interfaceOrientation.isLandscape
        }
        class_addMethod(
            type(of: root), selector,
            imp_implementationWithBlock(locked), "B@:"
        )

        // SwiftUI's root controller passes the question on to the
        // NavigationStack's own hosting controller, which says no - so
        // the override above was never even asked, and iPadOS never
        // granted the lock. Answering for itself is what makes it count.
        // Replaced rather than added, unlike the getter above: the root
        // controller's class implements this one itself, so an add is
        // refused. It is that one class only, and only this question -
        // nothing else SwiftUI passes to its children is touched.
        let childSelector = #selector(getter: UIViewController.childForInterfaceOrientationLock)
        let noChild: @convention(block) (AnyObject) -> UIViewController? = { _ in nil }
        class_replaceMethod(
            type(of: root), childSelector,
            imp_implementationWithBlock(noChild), "@@:"
        )
    }

    func application(
        _ application: UIApplication,
        supportedInterfaceOrientationsFor window: UIWindow?
    ) -> UIInterfaceOrientationMask {
        // An iPad is landscape on every screen, the login's portrait lock
        // included - Info.plist lists only the landscapes for it, and a
        // mask with nothing in common with that list is a crash.
        if LargeScreen.isActive || Self.lockedToLandscape { return .landscape }
        return Self.lockedToPortrait ? .portrait : .all
    }
}
