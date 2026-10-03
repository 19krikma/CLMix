import SwiftUI
import UIKit

/// Routes meter frames straight at the meter views currently on screen,
/// without going through any `@Published` state, and advances every one
/// of them off a single display link.
///
/// Frames arrive ~20x a second against the levels push's ~7x (see
/// METER_PUSH_INTERVAL_SECONDS in services/remote_server.py). Publishing
/// them through AppModel would re-render every channel strip in the grid
/// twenty times a second to move one bar - so this hands each frame to
/// the meter views directly instead, exactly as Android's
/// ChannelAdapter.updateMeters walks the visible holders rather than
/// calling notifyItemChanged.
///
/// The one display link matters as much as the one frame. A phone shows a
/// dozen strips at once, and a dozen CADisplayLinks - each waking the
/// main thread, taking its own timestamp and asking for its own redraw
/// every vsync - cost more between them than the bars they were there to
/// move. One link also means every bar on screen advances off exactly the
/// same clock, so a row of meters fed the same signal can't drift apart.
@MainActor
final class MeterCenter: NSObject {
    static let shared = MeterCenter()

    // Weak: a strip scrolled off the grid or dropped by a bank switch
    // takes its meter view with it, and nothing here should keep it
    // alive.
    private let views = NSHashTable<ChannelMeterUIView>.weakObjects()

    private var sequence: Int64 = 0
    private var frame: [Int: MeterLevels] = [:]

    private var displayLink: CADisplayLink?
    private var lastStepAt: CFTimeInterval = 0

    // NSObject only so CADisplayLink has an Objective-C target to call
    // tick() on - a pure Swift class cannot carry an @objc member.
    private override init() { super.init() }

    func register(_ view: ChannelMeterUIView) {
        views.add(view)

        // Start from the current picture rather than from silence - a
        // strip scrolled into view mid-show would otherwise have to wait
        // for the next frame that happens to differ.
        if let levels = frame[view.channel] {
            view.apply(sequence: sequence, levels: levels)
            startAnimating()
        }
    }

    func unregister(_ view: ChannelMeterUIView) {
        views.remove(view)
        if views.count == 0 { stopAnimating() }
    }

    func publish(sequence: Int64, frame: [Int: MeterLevels]) {
        self.sequence = sequence
        self.frame = frame

        var fed = false
        for view in views.allObjects {
            guard let levels = frame[view.channel] else { continue }
            view.apply(sequence: sequence, levels: levels)
            fed = true
        }

        if fed { startAnimating() }
    }

    /// Drops the retained frame and empties every bar - for a logout or a
    /// disconnect, where the last frame describes a mix that is no longer
    /// being fed and would otherwise sit frozen on screen.
    func clear() {
        frame = [:]
        sequence = 0
        views.allObjects.forEach { $0.reset() }
        stopAnimating()
    }

    /// Runs the link whenever anything on screen has somewhere to travel
    /// to. Idempotent - every path that hands out a fresh sample calls it.
    private func startAnimating() {
        guard displayLink == nil else { return }

        lastStepAt = 0

        let link = CADisplayLink(target: self, selector: #selector(tick))
        // .common so a fader drag or a sideways scroll of the channel
        // grid doesn't suspend the meters for the length of the gesture.
        link.add(to: .main, forMode: .common)
        displayLink = link
    }

    private func stopAnimating() {
        displayLink?.invalidate()
        displayLink = nil
        lastStepAt = 0
    }

    @objc private func tick(_ link: CADisplayLink) {
        let now = link.timestamp
        let elapsed = lastStepAt == 0 ? 0 : min(0.25, now - lastStepAt)
        lastStepAt = now

        // Batched across every meter on screen: one transaction for the
        // whole row rather than one per bar.
        CATransaction.begin()
        CATransaction.setDisableActions(true)

        var moving = false
        for view in views.allObjects {
            if view.step(elapsed: elapsed, now: now) { moving = true }
        }

        CATransaction.commit()

        // Idle strips stop the link entirely, rather than burning a frame
        // each vsync on bars that are already at the floor.
        if !moving { stopAnimating() }
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
///
/// Nothing here draws per frame. The ladder of slices is two 1px-wide
/// images - the lit gradient and the dimmed one - built once per bar
/// height and shared by every meter on screen; a moving bar is then only
/// a layer frame and a `contentsRect`, which the compositor handles off
/// the main thread. Drawing it instead, as this used to and as Android
/// still does, meant ~400 CoreGraphics fills and twice that many UIColor
/// allocations per bar per vsync: Android's Canvas turns those into a
/// hardware display list, where CoreGraphics rasterizes them on the CPU
/// and re-uploads the bitmap every frame, so the same code costs
/// something entirely different on the two platforms.
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
            setNeedsLayout()
        }
    }

    private final class Leg {
        var shown = ChannelMeterUIView.floorDb
        var target = ChannelMeterUIView.floorDb
        var rising = false
        var peakHold = ChannelMeterUIView.floorDb
        var peakHeldAt: CFTimeInterval = 0
        var seq: Int64 = -1

        // The unlit ladder underneath, the lit one cropped to the bar's
        // current height, and the peak-hold tick above it.
        let dim = CALayer()
        let lit = CALayer()
        let peak = CALayer()

        var x0: CGFloat = 0
        var barWidth: CGFloat = 0

        // What was last handed to the layers, so a vsync that doesn't
        // move this bar by a whole slice sets nothing at all.
        var litSlices = -1
        var peakY: CGFloat = .nan
    }

    private let legs = [Leg(), Leg()]

    // Slices down the whole bar, from the view's height. 0 until the
    // first layout.
    private var slices = 0

    override init(frame: CGRect) {
        super.init(frame: frame)
        isOpaque = false
        backgroundColor = .clear
        isUserInteractionEnabled = false

        for leg in legs {
            for layer in [leg.dim, leg.lit, leg.peak] {
                layer.isHidden = true
                layer.magnificationFilter = .nearest
                layer.minificationFilter = .nearest
                // The ladder is a vertical gradient stretched across the
                // bar's width, so the images are 1px wide and the layer
                // scales them out.
                layer.contentsGravity = .resize
                self.layer.addSublayer(layer)
            }
            leg.peak.contents = nil
            leg.peak.backgroundColor = Self.peakColor.cgColor
        }
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
        } else {
            MeterCenter.shared.register(self)
        }
    }

    /// `sequence` must advance only when the server sent a fresh frame.
    func apply(sequence: Int64, levels: MeterLevels) {
        applyLeg(legs[0], sequence, levels.leftPeak, levels.leftRms)
        applyLeg(legs[1], sequence, levels.rightPeak, levels.rightRms)
    }

    func reset() {
        for leg in legs {
            leg.shown = Self.floorDb
            leg.target = Self.floorDb
            leg.rising = false
            leg.peakHold = Self.floorDb
            leg.seq = -1
        }

        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for leg in legs { place(leg) }
        CATransaction.commit()
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

    /// Advances this meter's ballistics by `elapsed` and moves its layers
    /// to match. Returns whether anything is still off the floor, so
    /// MeterCenter can stop the link once the whole screen is idle.
    ///
    /// Called inside MeterCenter's own CATransaction - nothing here opens
    /// one of its own.
    fileprivate func step(elapsed: CFTimeInterval, now: CFTimeInterval) -> Bool {
        let active = stereo ? 2 : 1
        var moving = false

        for index in 0..<active {
            let leg = legs[index]
            advance(leg, elapsed: elapsed, now: now)

            if leg.shown > Self.floorDb || leg.peakHold > Self.floorDb {
                moving = true
            }

            place(leg)
        }

        return moving
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

    // MARK: - Layout

    override func layoutSubviews() {
        super.layoutSubviews()

        let h = bounds.height
        let active = stereo ? 2 : 1
        let gap: CGFloat = stereo ? Self.gapPoints : 0
        let barWidth = (bounds.width - gap * CGFloat(active - 1)) / CGFloat(active)

        guard h > 0, barWidth > 0 else { return }

        slices = max(1, Int((h / Self.slicePoints).rounded()))
        let ladder = Self.ladder(slices: slices)

        CATransaction.begin()
        CATransaction.setDisableActions(true)

        for (index, leg) in legs.enumerated() {
            guard index < active else {
                leg.dim.isHidden = true
                leg.lit.isHidden = true
                leg.peak.isHidden = true
                continue
            }

            leg.x0 = CGFloat(index) * (barWidth + gap)
            leg.barWidth = barWidth

            leg.dim.contents = ladder.dim
            leg.dim.frame = CGRect(x: leg.x0, y: 0, width: barWidth, height: h)
            leg.dim.isHidden = false

            leg.lit.contents = ladder.lit

            // The bar hasn't moved, but everything it was measured
            // against has - so the cached geometry is no longer what the
            // layers are showing.
            leg.litSlices = -1
            leg.peakY = .nan
            place(leg)
        }

        CATransaction.commit()
    }

    /// Moves one leg's layers to wherever its ballistics now say, and
    /// nothing else. Cheap enough to call every vsync precisely because
    /// it usually finds nothing to do.
    private func place(_ leg: Leg) {
        let h = bounds.height
        guard h > 0, slices > 0, leg.barWidth > 0 else { return }

        let lit = min(slices, max(0, Int((Self.fraction(leg.shown) * Double(slices)).rounded())))

        if lit != leg.litSlices {
            leg.litSlices = lit

            if lit == 0 {
                leg.lit.isHidden = true
            } else {
                let fraction = CGFloat(lit) / CGFloat(slices)
                let litHeight = h * fraction

                leg.lit.frame = CGRect(
                    x: leg.x0, y: h - litHeight, width: leg.barWidth, height: litHeight
                )
                // Crops the ladder image to the same bottom fraction the
                // layer covers, so the lit slices land on exactly the
                // rows the unlit ones underneath them occupy.
                leg.lit.contentsRect = CGRect(x: 0, y: 1 - fraction, width: 1, height: fraction)
                leg.lit.isHidden = false
            }
        }

        if leg.peakHold <= Self.floorDb {
            if !leg.peak.isHidden { leg.peak.isHidden = true }
            leg.peakY = .nan
        } else {
            let y = h - CGFloat(Self.fraction(leg.peakHold)) * h - Self.peakThickness
            if y != leg.peakY {
                leg.peakY = y
                leg.peak.frame = CGRect(
                    x: leg.x0, y: y, width: leg.barWidth, height: Self.peakThickness
                )
            }
            if leg.peak.isHidden { leg.peak.isHidden = false }
        }
    }

    // MARK: - The ladder

    // Optional because CGImage creation can in principle fail: a bar with
    // no contents draws nothing, which is a meter that does not move
    // rather than a show that stops.
    private struct Ladder {
        let lit: CGImage?
        let dim: CGImage?
    }

    // Keyed on the slice count alone, which is the only thing the images
    // depend on - every strip on screen is the same height, so in
    // practice the whole app shares one pair. The colours are absolute
    // (sampled off the console) rather than themed, so nothing here has
    // to be rebuilt for dark mode.
    @MainActor private static var ladders: [Int: Ladder] = [:]

    @MainActor private static func ladder(slices: Int) -> Ladder {
        if let cached = ladders[slices] { return cached }

        var lit = [UInt8](repeating: 0, count: slices * 4)
        var dim = [UInt8](repeating: 0, count: slices * 4)

        for i in 0..<slices {
            let db = floorDb + (Double(i) + 0.5) / Double(slices) * -floorDb
            let rgb = gradientAt(db)

            // Slice 0 is the bottom of the bar; row 0 is the top of the
            // image.
            let row = (slices - 1 - i) * 4

            lit[row] = rgb.r
            lit[row + 1] = rgb.g
            lit[row + 2] = rgb.b
            lit[row + 3] = 255

            dim[row] = UInt8(Double(rgb.r) * dimFactor)
            dim[row + 1] = UInt8(Double(rgb.g) * dimFactor)
            dim[row + 2] = UInt8(Double(rgb.b) * dimFactor)
            dim[row + 3] = 255
        }

        let ladder = Ladder(
            lit: image(lit, height: slices),
            dim: image(dim, height: slices)
        )
        ladders[slices] = ladder
        return ladder
    }

    private static func image(_ bytes: [UInt8], height: Int) -> CGImage? {
        guard let provider = CGDataProvider(data: Data(bytes) as CFData) else { return nil }

        return CGImage(
            width: 1, height: height,
            bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
            provider: provider, decode: nil,
            // The ladder's slice edges are the point of it - interpolating
            // between rows would smear them into a continuous gradient.
            shouldInterpolate: false,
            intent: .defaultIntent
        )
    }

    private static func fraction(_ db: Double) -> Double {
        min(1, max(0, (db - floorDb) / -floorDb))
    }

    /// Linear interpolation between the stops bracketing this dB.
    private static func gradientAt(_ db: Double) -> (r: UInt8, g: UInt8, b: UInt8) {
        var low = gradient.first!
        var high = gradient.last!

        for i in 0..<(gradient.count - 1) {
            if db >= gradient[i].db && db <= gradient[i + 1].db {
                low = gradient[i]
                high = gradient[i + 1]
                break
            }
        }

        let span = high.db - low.db
        let ratio = span == 0 ? 0 : (db - low.db) / span

        return (
            UInt8((low.r + (high.r - low.r) * ratio).rounded()),
            UInt8((low.g + (high.g - low.g) * ratio).rounded()),
            UInt8((low.b + (high.b - low.b) * ratio).rounded())
        )
    }

    // MARK: - Constants

    static let floorDb = -60.0

    private static let gapPoints: CGFloat = 1.5
    private static let slicePoints: CGFloat = 1.5
    private static let dimFactor = 0.22
    private static let peakThickness: CGFloat = 1
    private static let peakColor = UIColor(
        red: 0xE6 / 255, green: 0xED / 255, blue: 0xF3 / 255, alpha: 1
    )

    private static let riseTauSeconds = 0.045
    private static let riseMinDbPerSec = 30.0
    private static let releaseDbPerSec = 40.0
    private static let peakHoldSeconds: CFTimeInterval = 0.9
    private static let peakFallDbPerSec = 40.0

    // Sampled from the console's own meters, same stops as the desktop and
    // Android.
    private struct Stop {
        let db: Double
        let r: Double
        let g: Double
        let b: Double

        init(_ db: Double, _ hex: Int) {
            self.db = db
            self.r = Double((hex >> 16) & 0xFF)
            self.g = Double((hex >> 8) & 0xFF)
            self.b = Double(hex & 0xFF)
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
