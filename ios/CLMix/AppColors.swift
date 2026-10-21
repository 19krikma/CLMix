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
    // The little black readout beside a dial on the channel input sheet.
    // Deliberately black in both palettes rather than a themed surface:
    // it is a meter-style readout, the way a console prints its own
    // values, not a panel.
    static let clmixReadoutFill = Color(
        light: UIColor(red: 0x0B / 255, green: 0x0C / 255, blue: 0x0E / 255, alpha: 1),
        dark: UIColor(red: 0x0B / 255, green: 0x0C / 255, blue: 0x0E / 255, alpha: 1)
    )
    static let clmixReadoutStroke = Color(
        light: UIColor(red: 0x2A / 255, green: 0x2D / 255, blue: 0x33 / 255, alpha: 1),
        dark: UIColor(red: 0x2A / 255, green: 0x2D / 255, blue: 0x33 / 255, alpha: 1)
    )
    static let clmixReadoutText = Color(
        light: UIColor(red: 0xF2 / 255, green: 0xF4 / 255, blue: 0xF8 / 255, alpha: 1),
        dark: UIColor(red: 0xF2 / 255, green: 0xF4 / 255, blue: 0xF8 / 255, alpha: 1)
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
    // The connect screen's own outline: its boxes, rows, rule, fields and
    // the floating theme toggle. Near-black by day rather than the grey
    // below, because they are drawn over the wallpaper's frosted band
    // instead of over a flat surface - a grey line that reads cleanly on
    // the background goes soft against a picture, however far the band has
    // pushed it back. The same ink as the labels inside them, so the form
    // reads as one drawing. White at night, which is what that screen has
    // always drawn its boxes in.
    static let clmixLoginOutline = Color(
        light: UIColor(red: 0x1B / 255, green: 0x1D / 255, blue: 0x22 / 255, alpha: 1),
        dark: UIColor(red: 0xFF / 255, green: 0xFF / 255, blue: 0xFF / 255, alpha: 1)
    )
    static let clmixOutline = Color(
        light: UIColor(red: 0x9B / 255, green: 0xA0 / 255, blue: 0xA8 / 255, alpha: 1),
        dark: UIColor.white
    )

    // The EQ screen: one colour per band and one for both filters, kept
    // the same on the graph's handles and on the chips that select them.
    // Mid-brightness hues that hold up on both surfaces, so one value
    // serves both themes. Mirrors Android's eq_band_* colours.
    static let clmixEqBandLF = Color(red: 0xF0 / 255, green: 0x7A / 255, blue: 0x2E / 255)
    static let clmixEqBandLMF = Color(red: 0xD9 / 255, green: 0xA4 / 255, blue: 0x00 / 255)
    static let clmixEqBandHMF = Color(red: 0x21 / 255, green: 0xA8 / 255, blue: 0x70 / 255)
    static let clmixEqBandHF = Color(red: 0x3B / 255, green: 0x82 / 255, blue: 0xF6 / 255)
    static let clmixEqFilter = Color(red: 0x9B / 255, green: 0x6C / 255, blue: 0xF0 / 255)

    private init(light: UIColor, dark: UIColor) {
        self = Color(UIColor { $0.userInterfaceStyle == .dark ? dark : light })
    }
}
