import SwiftUI
import UIKit

/// Routes meter frames straight at the meter views currently on screen,
/// without going through any `@Published` state.
///
/// Frames arrive ~20x a second against the levels push's ~7x (see
/// METER_PUSH_INTERVAL_SECONDS in services/remote_server.py). Publishing
/// them through AppModel would re-render every channel strip in the grid
/// twenty times a second to move one bar - so this hands each frame to
/// the meter views directly instead, exactly as Android's
/// ChannelAdapter.updateMeters walks the visible holders rather than
/// calling notifyItemChanged.
@MainActor
final class MeterCenter {
    static let shared = MeterCenter()

    // Weak: a strip scrolled off the grid or dropped by a bank switch
    // takes its meter view with it, and nothing here should keep it
    // alive.
    private let views = NSHashTable<ChannelMeterUIView>.weakObjects()

    private var sequence: Int64 = 0
    private var frame: [Int: MeterLevels] = [:]

    private init() {}

    func register(_ view: ChannelMeterUIView) {
        views.add(view)

        // Start from the current picture rather than from silence - a
        // strip scrolled into view mid-show would otherwise have to wait
        // for the next frame that happens to differ.
        if let levels = frame[view.channel] {
            view.apply(sequence: sequence, levels: levels)
        }
    }

    func unregister(_ view: ChannelMeterUIView) {
        views.remove(view)
    }

    func publish(sequence: Int64, frame: [Int: MeterLevels]) {
        self.sequence = sequence
        self.frame = frame

        for view in views.allObjects {
            guard let levels = frame[view.channel] else { continue }
            view.apply(sequence: sequence, levels: levels)
        }
    }

    /// Drops the retained frame and empties every bar - for a logout or a
    /// disconnect, where the last frame describes a mix that is no longer
    /// being fed and would otherwise sit frozen on screen.
    func clear() {
        frame = [:]
        sequence = 0
        views.allObjects.forEach { $0.reset() }
    }
}

/// Post-fader meter drawn beside a channel's fader, mirroring the desktop
/// app's AuxLevelsPanel meter and Android's ChannelMeterView: one bar for
/// a mono channel, two narrow bars sharing the same total width for a
/// stereo one, so a strip never changes width depending on the console's
/// configuration.
///
/// The scale is the console's own 0..-60 dB, NOT the fader's -150..+10 -
/// the two are not interchangeable and this is deliberately not drawn
/// against the fader's ruler.
///
/// Ballistics match the desktop's: the bar is always falling, and each
/// arriving sample pushes it back up. A sample is a one-shot push rather
/// than a level to settle on, which is why apply() takes a sequence that
/// only advances when the server actually sent something new -
/// re-applying the same latched value every frame would hold the bar up
/// forever.
final class ChannelMeterUIView: UIView {
    var channel: Int = 0 {
        didSet {
            guard channel != oldValue else { return }
            // A view reused for a different channel must not inherit the
            // old one's bar position.
            reset()
        }
    }

    var stereo: Bool = false {
        didSet {
            guard stereo != oldValue else { return }
            setNeedsDisplay()
        }
    }

    private final class Leg {
        var shown = ChannelMeterUIView.floorDb
        var target = ChannelMeterUIView.floorDb
        var rising = false
        var peakHold = ChannelMeterUIView.floorDb
        var peakHeldAt: CFTimeInterval = 0
        var seq: Int64 = -1
    }

    private let legs = [Leg(), Leg()]
    private var displayLink: CADisplayLink?
    private var lastFrameAt: CFTimeInterval = 0

    override init(frame: CGRect) {
        super.init(frame: frame)
        isOpaque = false
        backgroundColor = .clear
        isUserInteractionEnabled = false
        contentMode = .redraw
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) is not used - this view is created from SwiftUI")
    }

    // Nothing here wants a size of its own: the strip decides how wide
    // the meter is and the fader row decides how tall. Left at a plain
    // UIView's zero intrinsic size, SwiftUI would take that literally and
    // collapse the bar to nothing.
    override var intrinsicContentSize: CGSize {
        CGSize(width: UIView.noIntrinsicMetric, height: UIView.noIntrinsicMetric)
    }

    // Registration follows the view's own life on screen, so a strip that
    // scrolls away or is dropped by a bank switch stops being fed without
    // anything else having to notice.
    override func didMoveToWindow() {
        super.didMoveToWindow()

        if window == nil {
            MeterCenter.shared.unregister(self)
            stopAnimating()
        } else {
            MeterCenter.shared.register(self)
        }
    }

    /// `sequence` must advance only when the server sent a fresh frame.
    func apply(sequence: Int64, levels: MeterLevels) {
        applyLeg(legs[0], sequence, levels.leftPeak, levels.leftRms)
        applyLeg(legs[1], sequence, levels.rightPeak, levels.rightRms)
        startAnimating()
    }

    func reset() {
        for leg in legs {
            leg.shown = Self.floorDb
            leg.target = Self.floorDb
            leg.rising = false
            leg.peakHold = Self.floorDb
            leg.seq = -1
        }

        stopAnimating()
        setNeedsDisplay()
    }

    private func applyLeg(_ leg: Leg, _ sequence: Int64, _ peak: Double?, _ rms: Double?) {
        guard leg.seq != sequence else { return }
        leg.seq = sequence

        // The console reports peak and RMS independently and either may
        // be at its floor sentinel, so drive the bar from whichever is
        // actually present.
        let target = max(rms ?? Self.floorDb, peak ?? Self.floorDb)
        leg.target = target
        leg.rising = target > leg.shown
    }

    // MARK: - Animation

    private func startAnimating() {
        guard displayLink == nil else { return }

        lastFrameAt = CACurrentMediaTime()

        let link = CADisplayLink(target: self, selector: #selector(tick))
        // .common so a fader drag or a sideways scroll of the channel
        // grid doesn't suspend the meters for the length of the gesture.
        link.add(to: .main, forMode: .common)
        displayLink = link
    }

    private func stopAnimating() {
        displayLink?.invalidate()
        displayLink = nil
        lastFrameAt = 0
    }

    @objc private func tick() {
        setNeedsDisplay()
    }

    // MARK: - Drawing

    override func draw(_ rect: CGRect) {
        guard let context = UIGraphicsGetCurrentContext() else { return }

        let now = CACurrentMediaTime()
        let elapsed = lastFrameAt == 0 ? 0 : min(0.25, now - lastFrameAt)
        lastFrameAt = now

        let active = stereo ? 2 : 1
        let gap: CGFloat = stereo ? Self.gapPoints : 0
        let barWidth = (bounds.width - gap * CGFloat(active - 1)) / CGFloat(active)

        var moving = false

        for index in 0..<active {
            let leg = legs[index]
            advance(leg, elapsed: elapsed, now: now)

            if leg.shown > Self.floorDb || leg.peakHold > Self.floorDb {
                moving = true
            }

            drawLeg(context, leg, x0: CGFloat(index) * (barWidth + gap), barWidth: barWidth)
        }

        // Idle strips stop redrawing entirely, rather than burning a
        // frame each vsync on a bar that is already at the floor.
        if !moving {
            stopAnimating()
        }
    }

    private func advance(_ leg: Leg, elapsed: CFTimeInterval, now: CFTimeInterval) {
        if leg.rising {
            // Ease up to the sample, never overshooting it. Once reached
            // the push is spent and the release takes over again.
            let decay = exp(-elapsed / Self.riseTauSeconds)
            let eased = leg.target + (leg.shown - leg.target) * decay
            leg.shown = min(leg.target, max(eased, leg.shown + Self.riseMinDbPerSec * elapsed))
            if leg.shown >= leg.target { leg.rising = false }
        } else {
            leg.shown = max(Self.floorDb, leg.shown - Self.releaseDbPerSec * elapsed)
        }

        // Tracked against the incoming sample rather than the animated
        // bar, so a brief transient still marks its true peak while the
        // bar is still sliding up to it.
        if leg.target >= leg.peakHold {
            leg.peakHold = leg.target
            leg.peakHeldAt = now
        } else if now - leg.peakHeldAt > Self.peakHoldSeconds {
            leg.peakHold = max(leg.shown, leg.peakHold - Self.peakFallDbPerSec * elapsed)
        }
    }

    private func drawLeg(_ context: CGContext, _ leg: Leg, x0: CGFloat, barWidth: CGFloat) {
        let h = bounds.height
        guard h > 0, barWidth > 0 else { return }

        let slices = max(1, Int((h / Self.slicePoints).rounded()))
        let lit = Int((fraction(leg.shown) * Double(slices)).rounded())
        let sliceHeight = h / CGFloat(slices)

        for i in 0..<slices {
            let db = Self.floorDb + (Double(i) + 0.5) / Double(slices) * -Self.floorDb
            let rgb = gradientAt(db)

            context.setFillColor(i < lit ? rgb.cgColor : Self.dim(rgb).cgColor)

            let bottom = h - CGFloat(i) * sliceHeight
            context.fill(CGRect(x: x0, y: bottom - sliceHeight, width: barWidth, height: sliceHeight))
        }

        if leg.peakHold > Self.floorDb {
            let y = h - CGFloat(fraction(leg.peakHold)) * h
            context.setFillColor(Self.peakColor.cgColor)
            context.fill(
                CGRect(x: x0, y: y - Self.peakThickness, width: barWidth, height: Self.peakThickness)
            )
        }
    }

    private func fraction(_ db: Double) -> Double {
        min(1, max(0, (db - Self.floorDb) / -Self.floorDb))
    }

    /// Linear interpolation between the stops bracketing this dB.
    private func gradientAt(_ db: Double) -> UIColor {
        var low = Self.gradient.first!
        var high = Self.gradient.last!

        for i in 0..<(Self.gradient.count - 1) {
            if db >= Self.gradient[i].db && db <= Self.gradient[i + 1].db {
                low = Self.gradient[i]
                high = Self.gradient[i + 1]
                break
            }
        }

        let span = high.db - low.db
        let ratio = span == 0 ? 0 : (db - low.db) / span

        return UIColor(
            red: low.r + (high.r - low.r) * ratio,
            green: low.g + (high.g - low.g) * ratio,
            blue: low.b + (high.b - low.b) * ratio,
            alpha: 1
        )
    }

    /// Unlit slices keep their hue at low brightness, as on the desk.
    private static func dim(_ color: UIColor) -> UIColor {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        color.getRed(&r, green: &g, blue: &b, alpha: &a)
        return UIColor(red: r * dimFactor, green: g * dimFactor, blue: b * dimFactor, alpha: 1)
    }

    // MARK: - Constants

    static let floorDb = -60.0

    private static let gapPoints: CGFloat = 1.5
    private static let slicePoints: CGFloat = 1.5
    private static let dimFactor: CGFloat = 0.22
    private static let peakThickness: CGFloat = 1
    private static let peakColor = UIColor(
        red: 0xE6 / 255, green: 0xED / 255, blue: 0xF3 / 255, alpha: 1
    )

    private static let riseTauSeconds = 0.045
    private static let riseMinDbPerSec = 30.0
    private static let releaseDbPerSec = 40.0
    private static let peakHoldSeconds: CFTimeInterval = 0.9
    private static let peakFallDbPerSec = 40.0

    // Sampled from the SD7's own meters, same stops as the desktop and
    // Android.
    private struct Stop {
        let db: Double
        let r: CGFloat
        let g: CGFloat
        let b: CGFloat

        init(_ db: Double, _ hex: Int) {
            self.db = db
            self.r = CGFloat((hex >> 16) & 0xFF) / 255
            self.g = CGFloat((hex >> 8) & 0xFF) / 255
            self.b = CGFloat(hex & 0xFF) / 255
        }
    }

    private static let gradient: [Stop] = [
        Stop(-60, 0x0A30C8),
        Stop(-46, 0x00A0E8),
        Stop(-34, 0x00D24A),
        Stop(-20, 0x7ADA00),
        Stop(-14, 0xD8D000),
        Stop(-10, 0xE8401C),
        Stop(0, 0xFF1A10),
    ]
}

/// SwiftUI's handle on the meter above. Deliberately thin: everything
/// that moves is driven by MeterCenter feeding the UIView directly, so
/// this only carries the two things that come from channel state.
struct ChannelMeterView: UIViewRepresentable {
    let channel: Int
    let stereo: Bool

    func makeUIView(context: Context) -> ChannelMeterUIView {
        let view = ChannelMeterUIView()
        view.channel = channel
        view.stereo = stereo
        return view
    }

    func updateUIView(_ view: ChannelMeterUIView, context: Context) {
        view.channel = channel
        view.stereo = stereo
    }

    /// Takes whatever the strip offers in both axes - the enclosing
    /// `.frame(width: 10)` fixes the width, and the fader row's own
    /// height is what the bar should fill.
    func sizeThatFits(
        _ proposal: ProposedViewSize, uiView: ChannelMeterUIView, context: Context
    ) -> CGSize? {
        CGSize(width: proposal.width ?? 10, height: proposal.height ?? 0)
    }

    // No dismantleUIView: the view unregisters itself when it leaves the
    // window (see didMoveToWindow), and MeterCenter holds it weakly, so a
    // strip dropped by a bank switch falls out of the table on its own.
}
