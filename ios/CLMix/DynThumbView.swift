import SwiftUI

/// One dynamics section's curve in miniature: a strip's COMP or GATE
/// button.
///
/// Level in across, level out up, -60...0 dB both ways - the Dynamics
/// screen's graph for just this one section, with no grid, labels or
/// handles. The compressor reads as a line bending over above its
/// threshold, the gate as a line dropping away below its own, so the two
/// stacked buttons tell themselves apart without a word on either. Drawn
/// in the accent colour while the section is in and greyed while it is
/// out, like the EQ thumbnail above them. Before the console has reported
/// anything it is the plain diagonal. Mirrors Android's DynThumbView.
struct DynThumbView: View {
    /// True to draw the compressor's curve, false the gate's.
    let comp: Bool
    let section: DynSection?

    private static let minDb = -60.0
    private static let inset: CGFloat = 4

    var body: some View {
        let isIn = section?.isIn == true
        let color = isIn ? Color.clmixPrimary : Color.clmixOnSurfaceVariant
        // Out, the curve the section would make is still drawn, greyed.
        let shaped: DynSection? = section.map { var s = $0; s.isIn = true; return s }

        Canvas { context, size in
            let plot = CGRect(
                x: Self.inset, y: Self.inset,
                width: size.width - 2 * Self.inset, height: size.height - 2 * Self.inset
            )

            func point(_ input: Double, _ output: Double) -> CGPoint {
                let clamped = min(0, max(Self.minDb, output))
                return CGPoint(
                    x: plot.minX + CGFloat((input - Self.minDb) / -Self.minDb) * plot.width,
                    y: plot.maxY - CGFloat((clamped - Self.minDb) / -Self.minDb) * plot.height
                )
            }

            var unity = Path()
            unity.move(to: CGPoint(x: plot.minX, y: plot.maxY))
            unity.addLine(to: CGPoint(x: plot.maxX, y: plot.minY))
            context.stroke(unity, with: .color(Color.clmixOnSurfaceVariant.opacity(0.27)), lineWidth: 1)

            var curve = Path()
            let steps = max(24, Int(plot.width))
            for i in 0...steps {
                let input = Self.minDb + -Self.minDb * Double(i) / Double(steps)
                let output: Double
                if let shaped {
                    output = comp ? DynResponse.comp(shaped, input) : DynResponse.gate(shaped, input)
                } else {
                    output = input
                }
                let p = point(input, output)
                if i == 0 { curve.move(to: p) } else { curve.addLine(to: p) }
            }

            var fill = curve
            fill.addLine(to: CGPoint(x: plot.maxX, y: plot.maxY))
            fill.addLine(to: CGPoint(x: plot.minX, y: plot.maxY))
            fill.closeSubpath()

            var clipped = context
            clipped.clip(to: Path(plot))
            clipped.fill(fill, with: .color(color.opacity(isIn ? 0.2 : 0.1)))
            clipped.stroke(
                curve,
                with: .color(color.opacity(isIn ? 1 : 0.6)),
                style: StrokeStyle(lineWidth: 1.8, lineCap: .round, lineJoin: .round)
            )
        }
        .background(Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .contentShape(Rectangle())
    }
}
