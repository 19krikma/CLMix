import SwiftUI

/// A round dial that is turned by holding it and sliding sideways.
///
/// Deliberately not a slider: gain and trim are set in small deliberate
/// steps on a console, and a strip on a phone screen is far too short to
/// put 60 dB on. So the finger's distance from where it landed sets a
/// *rate* rather than a position - hold a little to the right and the
/// value climbs slowly, push further and it spins up, and the same to the
/// left brings it down. Nothing moves until a touch lands on the dial and
/// everything stops the moment it lifts, which is what keeps a pocket or
/// a stray brush from moving a head amp.
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
        // A sheet dismissed mid-turn would otherwise leave the ticker
        // running against a view that is no longer on screen.
        .onDisappear { turn.cancel() }
    }

    private var dragGesture: some Gesture {
        // minimumDistance 0 so the dial engages on touch-down: the whole
        // gesture is "hold here and lean", and waiting for travel would
        // mean the first thing the finger did was already spent.
        DragGesture(minimumDistance: 0)
            .onChanged { gesture in
                guard hasValue else { return }

                if !turn.engaged {
                    turn.begin(from: value, within: range, onChanged: onValueChanged)
                }

                turn.offset = gesture.translation.width
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

/// The turn itself: how far the finger is leaning, and the value that
/// lean is winding out.
///
/// A reference type rather than a pile of @State on the view because the
/// turning is driven off a ticker, not off drag callbacks - the finger
/// can be held perfectly still and still be asking the dial to keep
/// turning, and there are no move events at all in that case. A timer
/// block reading and writing the view struct's own state would be
/// reaching through a captured copy of it; here it has one object to talk
/// to for the whole gesture.
///
/// Everything in it runs on the main thread - the gesture callbacks
/// arrive there, and the ticker is scheduled on the main run loop - so
/// it is deliberately not actor-isolated, the same way DemoMixer drives
/// its own timers.
final class DialTurn: ObservableObject {
    @Published private(set) var engaged = false
    @Published private(set) var value: Double = 0

    /// How far the finger has travelled from where it landed, which is
    /// what sets the rate. Not @Published: it changes with every drag
    /// event and nothing is drawn from it directly.
    var offset: CGFloat = 0

    private var range: ClosedRange<Double> = 0...1
    private var onChanged: ((Double) -> Void)?
    private var lastTickAt = Date.distantPast
    private var ticker: Timer?

    func begin(
        from start: Double, within range: ClosedRange<Double>,
        onChanged: @escaping (Double) -> Void
    ) {
        self.range = range
        self.onChanged = onChanged
        value = min(range.upperBound, max(range.lowerBound, start))
        offset = 0
        lastTickAt = Date()
        engaged = true

        stopTicking()

        // .common rather than scheduledTimer's default mode, which is
        // suspended for the whole length of a touch - which is exactly
        // when this has to run.
        let timer = Timer(timeInterval: Self.tickSeconds, repeats: true) { [weak self] _ in
            // Timers added to the main run loop fire on the main thread,
            // which is where every other part of this object is driven
            // from too.
            self?.tick()
        }
        RunLoop.main.add(timer, forMode: .common)
        ticker = timer
    }

    /// Ends the turn, returning the value it settled on.
    func end() -> Double? {
        guard engaged else { return nil }

        stopTicking()
        engaged = false
        offset = 0
        return value
    }

    /// Ends it without reporting anything - the dial went away mid-turn.
    func cancel() {
        stopTicking()
        engaged = false
        offset = 0
    }

    private func stopTicking() {
        ticker?.invalidate()
        ticker = nil
    }

    // The run loop holds the timer, not this object, so one left running
    // would outlive the dial it belongs to - firing against a nil weak
    // self forever rather than stopping.
    deinit {
        ticker?.invalidate()
    }

    private func tick() {
        guard engaged else { return }

        let now = Date()
        let seconds = now.timeIntervalSince(lastTickAt)
        lastTickAt = now

        guard abs(offset) > Self.deadZone else { return }

        let direction: Double = offset > 0 ? 1 : -1
        let travel = Double(abs(offset) - Self.deadZone)

        // Squared so a small hold creeps for fine work while a big one
        // spins - linear made the far end unusably slow for 60 dB of
        // range.
        let perSecond = min(
            Self.maxUnitsPerSecond, Self.unitsPerSecondAtOnePoint * travel * travel
        )

        let next = min(
            range.upperBound,
            max(range.lowerBound, value + direction * perSecond * seconds)
        )

        guard next != value else { return }

        value = next
        onChanged?(next)
    }

    // Opens at the lower left and sweeps to the lower right.
    static let sweepStart = 135.0
    static let sweepDegrees = 270.0

    // How far the finger travels before anything moves, so resting a
    // thumb on the dial doesn't drift it.
    private static let deadZone: CGFloat = 6

    private static let tickSeconds = 1.0 / 60

    // At one point past the dead zone; squared with distance from there.
    private static let unitsPerSecondAtOnePoint = 0.06
    private static let maxUnitsPerSecond = 40.0
}
