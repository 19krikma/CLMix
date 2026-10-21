import SwiftUI

/// A channel's EQ curve in miniature: the strip's EQ button.
///
/// The same response the EQ screen draws (EqResponse), squeezed into the
/// width of a strip - no grid labels and no handles, just the 0 dB line
/// and the curve, so a row of strips shows at a glance which channels are
/// cut, boosted or filtered. Drawn in the accent colour while the EQ is in
/// and greyed while it is out, the way the full graph does. Before the
/// console has reported anything it is a flat line. Mirrors Android's
/// EqThumbView.
struct EqThumbView: View {
    let eq: ChannelEq?

    // Tighter than the full graph's ±20: in 40pt of height a few dB of
    // boost has to read as a shape rather than a ripple. A bigger move,
    // or a filter's skirt, simply runs into the edge.
    private static let dbSpan = 12.0
    private static let minHz = 20.0
    private static let maxHz = 20000.0
    private static let inset: CGFloat = 4

    var body: some View {
        let eqOut = eq?.eqIn == false
        let color = (eq == nil || eqOut) ? Color.clmixOnSurfaceVariant : Color.clmixPrimary

        Canvas { context, size in
            let left = Self.inset
            let right = size.width - Self.inset
            let top = Self.inset
            let zeroY = size.height / 2

            var zero = Path()
            zero.move(to: CGPoint(x: left, y: zeroY))
            zero.addLine(to: CGPoint(x: right, y: zeroY))
            context.stroke(zero, with: .color(Color.clmixOnSurfaceVariant.opacity(0.35)), lineWidth: 1)

            var curve = Path()
            let steps = max(24, Int(right - left))

            for i in 0...steps {
                let fraction = Double(i) / Double(steps)
                let hz = Self.minHz * pow(Self.maxHz / Self.minHz, fraction)
                // EQ out: the shape it would have, greyed - as on the screen.
                let raw = eq.map { EqResponse.totalDb($0, hz: hz, asIfIn: eqOut) } ?? 0
                let db = min(Self.dbSpan, max(-Self.dbSpan, raw))
                let point = CGPoint(
                    x: left + (right - left) * CGFloat(fraction),
                    y: zeroY - CGFloat(db / Self.dbSpan) * (zeroY - top)
                )
                if i == 0 { curve.move(to: point) } else { curve.addLine(to: point) }
            }

            var fill = curve
            fill.addLine(to: CGPoint(x: right, y: zeroY))
            fill.addLine(to: CGPoint(x: left, y: zeroY))
            fill.closeSubpath()
            context.fill(fill, with: .color(color.opacity(eqOut ? 0.12 : 0.24)))

            context.stroke(
                curve,
                with: .color(color.opacity(eqOut ? 0.6 : 1)),
                style: StrokeStyle(lineWidth: 1.8, lineCap: .round, lineJoin: .round)
            )
        }
        .background(Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .contentShape(Rectangle())
    }
}
