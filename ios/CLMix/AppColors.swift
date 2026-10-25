import SwiftUI

/// The app's palette, ported 1:1 from Android's colors.xml so both
/// platforms read as the same app - each color here is the exact hex
/// Android uses for the same semantic role. CLMix is dark at all times,
/// so there is one value per color.
extension Color {
    static let clmixPrimary = Color(uiColor: UIColor(red: 0x6C / 255, green: 0x93 / 255, blue: 0xFF / 255, alpha: 1))
    static let clmixOnPrimary = Color(uiColor: UIColor(red: 0x0B / 255, green: 0x15 / 255, blue: 0x30 / 255, alpha: 1))
    static let clmixSecondary = Color(uiColor: UIColor(red: 0x3F / 255, green: 0xCF / 255, blue: 0x8E / 255, alpha: 1))
    static let clmixOnSecondary = Color(uiColor: UIColor(red: 0x05 / 255, green: 0x2A / 255, blue: 0x18 / 255, alpha: 1))
    static let clmixBackground = Color(uiColor: UIColor(red: 0x12 / 255, green: 0x13 / 255, blue: 0x17 / 255, alpha: 1))
    static let clmixSurface = Color(uiColor: UIColor(red: 0x1B / 255, green: 0x1D / 255, blue: 0x22 / 255, alpha: 1))
    static let clmixOnSurface = Color(uiColor: UIColor(red: 0xEC / 255, green: 0xED / 255, blue: 0xF0 / 255, alpha: 1))
    static let clmixSurfaceVariant = Color(uiColor: UIColor(red: 0x25 / 255, green: 0x27 / 255, blue: 0x2E / 255, alpha: 1))
    static let clmixOnSurfaceVariant = Color(uiColor: UIColor(red: 0xA9 / 255, green: 0xAC / 255, blue: 0xB4 / 255, alpha: 1))
    static let clmixMuteActive = Color(uiColor: UIColor(red: 0xFF / 255, green: 0x6B / 255, blue: 0x62 / 255, alpha: 1))
    // The little black readout beside a dial on the channel input sheet.
    // Deliberately black rather than the surface color: it is a
    // meter-style readout, the way a console prints its own
    // values, not a panel.
    static let clmixReadoutFill = Color(uiColor: UIColor(red: 0x0B / 255, green: 0x0C / 255, blue: 0x0E / 255, alpha: 1))
    static let clmixReadoutStroke = Color(uiColor: UIColor(red: 0x2A / 255, green: 0x2D / 255, blue: 0x33 / 255, alpha: 1))
    static let clmixReadoutText = Color(uiColor: UIColor(red: 0xF2 / 255, green: 0xF4 / 255, blue: 0xF8 / 255, alpha: 1))
    static let clmixMuteInactive = Color(uiColor: UIColor(red: 0x33 / 255, green: 0x36 / 255, blue: 0x3C / 255, alpha: 1))
    static let clmixOnMuteInactive = Color(uiColor: UIColor(red: 0xD3 / 255, green: 0xD6 / 255, blue: 0xDC / 255, alpha: 1))
    static let clmixTrackBackground = Color(uiColor: UIColor(red: 0x33 / 255, green: 0x36 / 255, blue: 0x3C / 255, alpha: 1))
    // The connect screen's own outline: its boxes, rows, rule, fields and
    // the floating permissions button.
    static let clmixLoginOutline = Color(uiColor: UIColor(red: 0xFF / 255, green: 0xFF / 255, blue: 0xFF / 255, alpha: 1))
    // Discovered/Manual box borders and unselected server rows on
    // ConnectView.
    static let clmixOutline = Color(uiColor: UIColor.white)

    // The EQ screen: one colour per band and one for both filters, kept
    // the same on the graph's handles and on the chips that select them.
    // Mirrors Android's eq_band_* colours.
    static let clmixEqBandLF = Color(red: 0xF0 / 255, green: 0x7A / 255, blue: 0x2E / 255)
    static let clmixEqBandLMF = Color(red: 0xD9 / 255, green: 0xA4 / 255, blue: 0x00 / 255)
    static let clmixEqBandHMF = Color(red: 0x21 / 255, green: 0xA8 / 255, blue: 0x70 / 255)
    static let clmixEqBandHF = Color(red: 0x3B / 255, green: 0x82 / 255, blue: 0xF6 / 255)
    static let clmixEqFilter = Color(red: 0x9B / 255, green: 0x6C / 255, blue: 0xF0 / 255)
}
