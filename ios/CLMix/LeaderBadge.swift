import SwiftUI

/// The "★ Leader" pill next to a snapshot Leader's name in chat, and the
/// colours a leader can pick for it in ProfileView. The server keeps the
/// choice and stamps it on each message, so everyone sees the colour the
/// leader picked. Mirrors Android's LeaderBadge.
struct LeaderBadge: View {
    static let defaultHex = "#D4AF37"

    /// What ProfileView offers, gold first.
    static let palette = [
        "#D4AF37", "#F28C28", "#E5484D", "#E54887", "#8E4EC6",
        "#3E7BFA", "#12A594", "#46A758", "#9BA1A6",
    ]

    let hex: String
    var fontSize: CGFloat = 11

    var body: some View {
        let (fill, ink) = Self.colors(hex)
        Text("\u{2605} Leader")
            .font(.system(size: fontSize, weight: .bold))
            .foregroundStyle(ink)
            .padding(.horizontal, 7)
            .padding(.vertical, 1)
            .background(fill)
            .clipShape(Capsule())
    }

    static func rgb(_ hex: String) -> (Double, Double, Double) {
        var text = hex
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, let value = UInt32(text, radix: 16) else {
            return rgb(defaultHex)
        }
        return (Double((value >> 16) & 0xFF), Double((value >> 8) & 0xFF), Double(value & 0xFF))
    }

    static func color(_ hex: String) -> Color {
        let (r, g, b) = rgb(hex)
        return Color(red: r / 255, green: g / 255, blue: b / 255)
    }

    /// The fill, and dark ink on light colours or white on dark ones.
    static func colors(_ hex: String) -> (Color, Color) {
        let (r, g, b) = rgb(hex)
        let luma = 0.299 * r + 0.587 * g + 0.114 * b
        let ink = luma > 150 ? Color(red: 0x1A / 255, green: 0x1A / 255, blue: 0x1A / 255) : Color.white
        return (color(hex), ink)
    }
}
