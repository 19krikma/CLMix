import SwiftUI

/// The app's palette, ported 1:1 from Android's colors.xml/values-night's
/// colors.xml so both platforms read as the same app in both themes -
/// each color here is the exact hex Android resolves for the same
/// semantic role, switching on the trait collection the way Android's
/// day/night resource qualifiers do.
extension Color {
    static let clmixPrimary = Color(
        light: UIColor(red: 0x3B / 255, green: 0x6D / 255, blue: 0xF6 / 255, alpha: 1),
        dark: UIColor(red: 0x6C / 255, green: 0x93 / 255, blue: 0xFF / 255, alpha: 1)
    )
    static let clmixOnPrimary = Color(
        light: UIColor(red: 0xFF / 255, green: 0xFF / 255, blue: 0xFF / 255, alpha: 1),
        dark: UIColor(red: 0x0B / 255, green: 0x15 / 255, blue: 0x30 / 255, alpha: 1)
    )
    static let clmixSecondary = Color(
        light: UIColor(red: 0x1F / 255, green: 0xAA / 255, blue: 0x6B / 255, alpha: 1),
        dark: UIColor(red: 0x3F / 255, green: 0xCF / 255, blue: 0x8E / 255, alpha: 1)
    )
    static let clmixOnSecondary = Color(
        light: UIColor(red: 0xFF / 255, green: 0xFF / 255, blue: 0xFF / 255, alpha: 1),
        dark: UIColor(red: 0x05 / 255, green: 0x2A / 255, blue: 0x18 / 255, alpha: 1)
    )
    static let clmixBackground = Color(
        light: UIColor(red: 0xF4 / 255, green: 0xF5 / 255, blue: 0xF7 / 255, alpha: 1),
        dark: UIColor(red: 0x12 / 255, green: 0x13 / 255, blue: 0x17 / 255, alpha: 1)
    )
    static let clmixSurface = Color(
        light: UIColor(red: 0xFF / 255, green: 0xFF / 255, blue: 0xFF / 255, alpha: 1),
        dark: UIColor(red: 0x1B / 255, green: 0x1D / 255, blue: 0x22 / 255, alpha: 1)
    )
    static let clmixOnSurface = Color(
        light: UIColor(red: 0x1B / 255, green: 0x1D / 255, blue: 0x22 / 255, alpha: 1),
        dark: UIColor(red: 0xEC / 255, green: 0xED / 255, blue: 0xF0 / 255, alpha: 1)
    )
    static let clmixSurfaceVariant = Color(
        light: UIColor(red: 0xE7 / 255, green: 0xE9 / 255, blue: 0xEE / 255, alpha: 1),
        dark: UIColor(red: 0x25 / 255, green: 0x27 / 255, blue: 0x2E / 255, alpha: 1)
    )
    static let clmixOnSurfaceVariant = Color(
        light: UIColor(red: 0x54 / 255, green: 0x58 / 255, blue: 0x5F / 255, alpha: 1),
        dark: UIColor(red: 0xA9 / 255, green: 0xAC / 255, blue: 0xB4 / 255, alpha: 1)
    )
    static let clmixMuteActive = Color(
        light: UIColor(red: 0xE5 / 255, green: 0x47 / 255, blue: 0x3F / 255, alpha: 1),
        dark: UIColor(red: 0xFF / 255, green: 0x6B / 255, blue: 0x62 / 255, alpha: 1)
    )
    static let clmixMuteInactive = Color(
        light: UIColor(red: 0xD3 / 255, green: 0xD6 / 255, blue: 0xDC / 255, alpha: 1),
        dark: UIColor(red: 0x33 / 255, green: 0x36 / 255, blue: 0x3C / 255, alpha: 1)
    )
    static let clmixOnMuteInactive = Color(
        light: UIColor(red: 0x33 / 255, green: 0x36 / 255, blue: 0x3C / 255, alpha: 1),
        dark: UIColor(red: 0xD3 / 255, green: 0xD6 / 255, blue: 0xDC / 255, alpha: 1)
    )
    static let clmixTrackBackground = Color(
        light: UIColor(red: 0xD3 / 255, green: 0xD6 / 255, blue: 0xDC / 255, alpha: 1),
        dark: UIColor(red: 0x33 / 255, green: 0x36 / 255, blue: 0x3C / 255, alpha: 1)
    )
    // Discovered/Manual box borders and unselected server rows on
    // ConnectView - was hardcoded white on Android too until light mode
    // became reachable there; dark mode keeps the white it always had.
    static let clmixOutline = Color(
        light: UIColor(red: 0x9B / 255, green: 0xA0 / 255, blue: 0xA8 / 255, alpha: 1),
        dark: UIColor.white
    )

    private init(light: UIColor, dark: UIColor) {
        self = Color(UIColor { $0.userInterfaceStyle == .dark ? dark : light })
    }
}
