import SwiftUI

/// dB scale drawn beside a channel's level fader - numbers plus a
/// connector line pointing at the fader track, positioned via
/// AuxTaper.levelTickFractions so a tick's printed position always
/// matches exactly where dragging the fader there reports that dB
/// value. Mirrors the desktop app's AuxLevelsPanel._draw_level_ruler
/// and the Android app's LevelRulerView.kt.
///
/// The view is drawn wider than the column it is given, so the tick
/// lines run on past the numbers and reach the fader they point at
/// without taking any width from it. Everything but the lines is
/// transparent, so the overlap is invisible.
///
/// `Equatable` with nothing to compare: the ruler takes no inputs, so
/// every instance of it is identical and `.equatable()` at the call site
/// (see ChannelStripView.faderRow) lets SwiftUI skip rebuilding a dozen
/// of them every time anything else on the mixer screen changes. Laying
/// out twenty-odd positioned labels and lines per strip is otherwise the
/// most expensive thing in a strip that did not move.
struct LevelRulerView: View, Equatable {
    // Keeps the top/bottom labels (10, -∞) from being clipped by the
    // view's edge, since they'd otherwise be vertically centered on the
    // exact top/bottom endpoints.
    private let inset: CGFloat = 4

    // Leaves room for a three-glyph label ("-60") to the left of it.
    private let lineStartX: CGFloat = 18

    // Breathing room between a label and the tick line it hangs off.
    private let labelGap: CGFloat = 2.5

    var body: some View {
        GeometryReader { geo in
            let span = geo.size.height - 2 * inset
            let lineLength = max(0, geo.size.width - lineStartX)
            let labelWidth = lineStartX - labelGap

            ZStack(alignment: .topLeading) {
                ForEach(Array(AuxTaper.levelTicks.enumerated()), id: \.offset) { index, tick in
                    let fraction = AuxTaper.levelTickFractions[index]
                    let y = inset + (1 - CGFloat(fraction)) * span

                    // Right-aligned so every label ends at the same
                    // place, just before its tick line: left-aligned, a
                    // single-character "0" or "5" trailed off toward the
                    // strip's edge and left a gap between the number and
                    // the fader it labels, while "-60" nearly closed it.
                    // Hanging them all off the line keeps the numbers up
                    // against the fader whatever their width.
                    Text(tick.label)
                        .font(.system(size: 9))
                        .foregroundStyle(Color.clmixOnSurfaceVariant)
                        .lineLimit(1)
                        .fixedSize()
                        .frame(width: labelWidth, alignment: .trailing)
                        .position(x: labelWidth / 2, y: y)

                    Rectangle()
                        .fill(Color.clmixOnSurfaceVariant.opacity(0.6))
                        .frame(width: lineLength, height: 1)
                        .position(x: lineStartX + lineLength / 2, y: y)
                }
            }
        }
    }
}
