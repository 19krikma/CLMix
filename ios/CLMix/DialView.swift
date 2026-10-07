import SwiftUI

/// A round dial that is turned by holding it and dragging.
///
/// Deliberately not a slider: gain and trim are set in small deliberate
/// steps on a console, and a strip on a phone screen is far too short to
/// put 60 dB on. So the value follows the finger's *movement* - dragging
/// right turns it up and left turns it down, dragging up or down does
/// the same at half the rate for finer work, and a finger held still
/// leaves it exactly where it is. How far each bit of movement turns it
/// scales with how fast the finger is going: a slow drag creeps for fine
/// work, a quick swipe covers the range. Nothing moves until a touch
/// lands on the dial and everything stops the moment it lifts, which is
/// what keeps a pocket or a stray brush from moving a head amp.
///
/// The ring around the dial fills with the value's position between the
/// ends of `range`, and the pointer turns with it, so where the value
/// sits is readable at a glance without reading the number.
///
/// Mirrors Android's DialView.kt.
struct DialView: View {
    /// What the console last reported. Ignored while the dial is being
    /// turned, in favour of the turn's own live value - the pushed one
    /// travels to the desk and back, which is far too slow a loop to draw
    /// a turning dial from.
    let value: Double
    let range: ClosedRange<Double>
    /// Greyed out and inert until the console has reported a value for
    /// this channel.
    let hasValue: Bool
    /// Called as the value turns, with the live value.
    let onValueChanged: (Double) -> Void
    /// Called once the finger lifts, with the value it settled on.
    let onTurnFinished: (Double) -> Void

    @StateObject private var turn = DialTurn()

    private var shown: Double { turn.engaged ? turn.value : value }

    private var fraction: CGFloat {
        guard range.upperBound > range.lowerBound else { return 0 }
        let f = (shown - range.lowerBound) / (range.upperBound - range.lowerBound)
        return CGFloat(min(1, max(0, f)))
    }

    var body: some View {
        GeometryReader { geo in
            let side = min(geo.size.width, geo.size.height)
            let radius = side / 2 - Self.ringWidth / 2
            let center = CGPoint(x: geo.size.width / 2, y: geo.size.height / 2)
            let knobRadius = max(0, radius - Self.ringWidth * 1.4)

            ZStack {
                // Opens at the lower left and sweeps to the lower right,
                // the way a console's own rotary markings run - never a
                // full circle, so the ends of the range are visible
                // rather than meeting.
                arc(to: 1, center: center, radius: radius)
                    .stroke(
                        Color.clmixTrackBackground,
                        style: StrokeStyle(lineWidth: Self.ringWidth, lineCap: .round)
                    )

                if hasValue {
                    arc(to: fraction, center: center, radius: radius)
                        .stroke(
                            Color.clmixPrimary,
                            style: StrokeStyle(lineWidth: Self.ringWidth, lineCap: .round)
                        )
                }

                // Engaged, the knob brightens - the one thing that says
                // the dial is live and about to move something on the
                // console.
                Circle()
                    .fill(Color.clmixMuteInactive.opacity(turn.engaged ? 1 : 0.78))
                    .frame(width: knobRadius * 2, height: knobRadius * 2)
                    .position(center)

                if hasValue {
                    // The pointer turns with the value: this is the part
                    // that reads as the dial spinning while it is held.
                    pointer(center: center, knobRadius: knobRadius)
                        .stroke(
                            Color.clmixOnMuteInactive,
                            style: StrokeStyle(lineWidth: Self.pointerWidth, lineCap: .round)
                        )
                }
            }
            .contentShape(Rectangle())
            .gesture(dragGesture)
        }
        .frame(width: Self.size, height: Self.size)
        .opacity(hasValue ? 1 : 0.45)
        .accessibilityAddTraits(.isButton)
        // A sheet dismissed mid-turn never delivers onEnded, which would
        // leave the dial stuck engaged.
        .onDisappear { turn.cancel() }
    }

    private var dragGesture: some Gesture {
        // minimumDistance 0 so the dial engages (and brightens) on
        // touch-down, and claims the touch before the sheet's scroll can;
        // DialTurn applies its own slop before anything moves.
        DragGesture(minimumDistance: 0)
            .onChanged { gesture in
                guard hasValue else { return }

                if !turn.engaged {
                    turn.begin(from: value, within: range, onChanged: onValueChanged)
                }

                turn.move(to: gesture.translation, at: gesture.time)
            }
            .onEnded { _ in
                guard turn.engaged, let settled = turn.end() else { return }
                // Always reported, throttling or not: this is the value
                // the operator actually chose, and the console has to end
                // up on it.
                onTurnFinished(settled)
            }
    }

    private func arc(to fraction: CGFloat, center: CGPoint, radius: CGFloat) -> Path {
        Path { path in
            path.addArc(
                center: center,
                radius: max(0, radius),
                startAngle: .degrees(DialTurn.sweepStart),
                endAngle: .degrees(DialTurn.sweepStart + DialTurn.sweepDegrees * Double(fraction)),
                clockwise: false
            )
        }
    }

    private func pointer(center: CGPoint, knobRadius: CGFloat) -> Path {
        let angle = Angle
            .degrees(DialTurn.sweepStart + DialTurn.sweepDegrees * Double(fraction))
            .radians
        let inner = knobRadius * 0.35
        let outer = knobRadius * 0.8

        return Path { path in
            path.move(
                to: CGPoint(x: center.x + cos(angle) * inner, y: center.y + sin(angle) * inner)
            )
            path.addLine(
                to: CGPoint(x: center.x + cos(angle) * outer, y: center.y + sin(angle) * outer)
            )
        }
    }

    private static let size: CGFloat = 68
    private static let ringWidth: CGFloat = 5
    private static let pointerWidth: CGFloat = 2.5
}

/// The turn itself: where the finger last was, and the value its
/// movement is winding out.
///
/// A reference type rather than a pile of @State on the view so the
/// gesture has one object to talk to for its whole length, rather than
/// reading and writing view state through a captured copy of the struct.
///
/// Everything in it runs on the main thread - the gesture callbacks
/// arrive there - so it is deliberately not actor-isolated.
final class DialTurn: ObservableObject {
    @Published private(set) var engaged = false
    @Published private(set) var value: Double = 0

    private var range: ClosedRange<Double> = 0...1
    private var onChanged: ((Double) -> Void)?

    // Past the slop yet - until then a resting thumb's wobble is ignored.
    private var dragging = false
    private var last = CGSize.zero
    private var lastMoveAt = Date.distantPast

    // Finger speed in points/s, smoothed: per-event speeds jitter a lot
    // at 120 Hz, and that jitter would otherwise show as a lumpy turn.
    private var speed: Double = 0

    func begin(
        from start: Double, within range: ClosedRange<Double>,
        onChanged: @escaping (Double) -> Void
    ) {
        self.range = range
        self.onChanged = onChanged
        value = min(range.upperBound, max(range.lowerBound, start))
        dragging = false
        speed = 0
        engaged = true
    }

    /// Turns the dial by however far the finger moved since the last
    /// call. `translation` is from where the touch landed.
    func move(to translation: CGSize, at time: Date) {
        guard engaged else { return }

        if !dragging {
            guard hypot(translation.width, translation.height) >= Self.slop else { return }

            // Counted from here, not from touch-down, so crossing the
            // slop doesn't land as one jump.
            dragging = true
            last = translation
            lastMoveAt = time
            return
        }

        let dx = Double(translation.width - last.width)
        let dy = Double(translation.height - last.height)
        let seconds = max(0.001, time.timeIntervalSince(lastMoveAt))

        last = translation
        lastMoveAt = time

        let instant = hypot(dx, dy) / seconds
        speed += (instant - speed) * Self.speedSmoothing

        let unitsPerPoint = min(
            Self.maxUnitsPerPoint, Self.baseUnitsPerPoint + Self.accelUnitsPerPoint * speed
        )

        // Screen y grows downward, so up (negative dy) turns the value up.
        let travel = dx - dy * Self.verticalRate

        let next = min(
            range.upperBound,
            max(range.lowerBound, value + travel * unitsPerPoint)
        )

        guard next != value else { return }

        value = next
        onChanged?(next)
    }

    /// Ends the turn, returning the value it settled on.
    func end() -> Double? {
        guard engaged else { return nil }

        engaged = false
        return value
    }

    /// Ends it without reporting anything - the dial went away mid-turn.
    func cancel() {
        engaged = false
    }

    // Opens at the lower left and sweeps to the lower right.
    static let sweepStart = 135.0
    static let sweepDegrees = 270.0

    // How far the finger travels before anything moves, so resting a
    // thumb on the dial doesn't drift it.
    private static let slop: CGFloat = 6

    // Per point of finger travel: base when crawling, rising with speed
    // to max, so ~20pt is 1 dB done slowly and a quick swipe across the
    // sheet covers the whole 60 dB.
    private static let baseUnitsPerPoint = 0.05
    private static let accelUnitsPerPoint = 0.00025
    private static let maxUnitsPerPoint = 0.5

    // Up/down turns at this fraction of the sideways rate.
    private static let verticalRate = 0.5

    private static let speedSmoothing = 0.3
}
