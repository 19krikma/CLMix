import SwiftUI

/// dB scale drawn beside a channel's level fader - numbers plus a short
/// connector line pointing at the fader track, positioned via
/// AuxTaper.levelTickFractions so a tick's printed position always
/// matches exactly where dragging the fader there reports that dB
/// value. Mirrors the desktop app's AuxLevelsPanel._draw_level_ruler
/// and the Android app's LevelRulerView.kt.
struct LevelRulerView: View {
    // Keeps the top/bottom labels (+10, ∞) from being clipped by the
    // view's edge, since they'd otherwise be vertically centered on the
    // exact top/bottom endpoints.
    private let inset: CGFloat = 4
    private let lineStartX: CGFloat = 12
    private let labelWidth: CGFloat = 16

    var body: some View {
        GeometryReader { geo in
            let span = geo.size.height - 2 * inset
            let lineLength = max(0, geo.size.width - lineStartX)

            ZStack(alignment: .topLeading) {
                ForEach(Array(AuxTaper.levelTicks.enumerated()), id: \.offset) { index, tick in
                    let fraction = AuxTaper.levelTickFractions[index]
                    let y = inset + (1 - CGFloat(fraction)) * span

                    Text(tick.label)
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                        .frame(width: labelWidth, alignment: .leading)
                        .position(x: labelWidth / 2, y: y)

                    Rectangle()
                        .fill(Color.secondary.opacity(0.6))
                        .frame(width: lineLength, height: 1)
                        .position(x: lineStartX + lineLength / 2, y: y)
                }
            }
        }
    }
}
