import UIKit

/// Whether CLMix lays a screen out as a tablet rather than a phone. The
/// phone layouts fold most controls away behind toggles to give the
/// faders the screen; on a tablet those controls stay out.
///
/// Every iPad counts. Android draws the line at a 914dp shortest side
/// (the OnePlus Pad 3 used for testing - see LargeScreen.kt), which would
/// leave out every iPad short of the 13-inch ones; on iOS the idiom is
/// the line instead. The app requires full screen, so an iPad is never
/// handed a phone-sized slice of its screen.
enum LargeScreen {
    static var isActive: Bool { UIDevice.current.userInterfaceIdiom == .pad }

    /// Width each bank button gets on a tablet, where the number to a row
    /// follows the screen instead: about the width of a phone's own.
    static let bankTileWidth: CGFloat = 115

    /// Whether a screen this size keeps its side menu docked down the left:
    /// an iPad on its side. Upright, the faders want the width more, so the
    /// menu stays behind its button. Mirrors Android's wantsDockedMenu.
    static func docksMenu(_ size: CGSize) -> Bool {
        isActive && size.width > size.height
    }

    /// The docked menu's width - Android's side_menu_width. Narrower than a
    /// usual drawer: it holds a few buttons, and the rest is fader room.
    static let menuWidth: CGFloat = 220
}
