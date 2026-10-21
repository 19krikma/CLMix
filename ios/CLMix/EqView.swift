import SwiftUI

/// What on the EQ screen a control or a graph handle is aimed at.
enum EqTarget: Hashable {
    case band(Int)
    /// The high-pass, which the console calls lo_filter - it cuts lows.
    case loFilter
    /// The low-pass, the console's hi_filter.
    case hiFilter

    var color: Color {
        switch self {
        case .band(4): return .clmixEqBandLF
        case .band(3): return .clmixEqBandLMF
        case .band(2): return .clmixEqBandHMF
        case .band: return .clmixEqBandHF
        case .loFilter, .hiFilter: return .clmixEqFilter
        }
    }

    var chipLabel: String {
        switch self {
        case .band(let n): return EqResponse.label(n)
        case .loFilter: return "HPF"
        case .hiFilter: return "LPF"
        }
    }

    var handleLabel: String {
        switch self {
        case .band(4): return "LF"
        case .band(3): return "LM"
        case .band(2): return "HM"
        case .band(1): return "HF"
        case .band(let n): return "\(n)"
        case .loFilter: return "HP"
        case .hiFilter: return "LP"
        }
    }

    /// Low to high, the order the chips run in.
    static let all: [EqTarget] =
        [.loFilter] + EqResponse.bandsLowToHigh.map { .band($0) } + [.hiFilter]
}

/// One input channel's EQ, full screen and turned on its side.
///
/// Opened from the EQ button on a Full Mixer Control strip, and like that
/// screen it reaches the channel itself - every curve drawn here is what
/// FOH, the monitors and the recording all hear. Landscape because the
/// frequency axis wants the width (see AppDelegate.lockedToLandscape).
///
/// Two ways to change a band, which always agree: drag its handle on the
/// graph (frequency across, gain up and down, pinch for Q), or turn the
/// selected band's dials. Both go through AppModel.editEq, which moves the
/// picture at once and writes to the desk at a capped rate. Mirrors
/// Android's EqActivity.
struct EqView: View {
    @EnvironmentObject var model: AppModel

    @State private var selected: EqTarget = .band(EqResponse.lowBand)

    private var eq: ChannelEq {
        model.eq ?? ChannelEq(channel: model.eqChannel ?? 0)
    }

    private var channelName: String {
        let number = eq.channel
        return model.channels.first { $0.channel == number }?.name ?? "Ch \(number)"
    }

    var body: some View {
        VStack(spacing: 0) {
            topBar

            HStack(spacing: 8) {
                EqGraphView(
                    eq: eq,
                    selected: selected,
                    onSelect: { selected = $0 },
                    onDrag: { target, hz, db in
                        setFreq(target, hz)
                        if case .band(let n) = target, let db { setGain(n, db) }
                    },
                    onPinch: { band, q in setQ(band, q) },
                    onGestureEnd: { model.flushEqWrites() }
                )

                controlPanel
                    .frame(width: 300)
            }
            .padding(8)
        }
        .background(Color.clmixBackground.ignoresSafeArea())
        .statusBarHidden(true)
        .persistentSystemOverlays(.hidden)
    }

    // MARK: - Top bar

    private var topBar: some View {
        HStack(spacing: 8) {
            Button {
                model.closeEq()
            } label: {
                Image(systemName: "chevron.left")
                    .font(.system(size: 18, weight: .semibold))
                    .frame(width: 40, height: 40)
            }
            .accessibilityLabel("Back to the mixer")

            Text("\(eq.channel)")
                .font(.system(size: 13))
                .foregroundStyle(Color.clmixOnSurfaceVariant)

            Text(channelName)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(Color.clmixOnSurface)
                .lineLimit(1)

            Text("EQ")
                .font(.system(size: 15))
                .foregroundStyle(Color.clmixOnSurfaceVariant)

            if model.statusIsError && !model.statusMessage.isEmpty {
                Text(model.statusMessage)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixMuteActive)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)

            // The whole EQ section in or out. Bypassed, the graph still
            // draws the curve in grey so what switching back in would do
            // is visible. The filters have their own in/out and stay put.
            chip(
                eq.eqIn == false ? "EQ OUT" : "EQ IN",
                lit: eq.eqIn == true,
                color: .clmixPrimary,
                litText: .clmixOnPrimary,
                width: 80
            ) {
                model.editEq(.eqIn(eq.eqIn != true), toggle: true)
            }
            .frame(height: 40)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.clmixSurface)
    }

    // MARK: - Controls

    private var controlPanel: some View {
        VStack(spacing: 8) {
            HStack(spacing: 4) {
                ForEach(EqTarget.all, id: \.self) { target in
                    chip(target.chipLabel, lit: target == selected, color: target.color) {
                        selected = target
                    }
                }
            }
            .frame(height: 36)

            HStack {
                Text(title)
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(Color.clmixOnSurface)
                    .lineLimit(1)

                Spacer(minLength: 0)

                chip(isBand ? "ON" : "IN", lit: selectedIsOn, color: selected.color, width: 64) {
                    toggleSelected()
                }
                .frame(height: 36)
            }

            Spacer(minLength: 0)

            HStack(alignment: .top, spacing: 0) {
                dialColumn(
                    "Freq",
                    value: selectedFreq.map { log2($0) },
                    range: log2(EqRange.freq.lowerBound)...log2(EqRange.freq.upperBound),
                    readout: formatEqFreq(selectedFreq),
                    rate: 0.25
                ) { setFreq(selected, pow(2, $0)) }

                // Kept in the layout for a filter, just not shown, so the
                // frequency dial does not jump sideways between the two.
                dialColumn(
                    "Gain",
                    value: selectedBand?.gain,
                    range: EqRange.gain,
                    readout: formatEqGain(selectedBand?.gain),
                    rate: 0.6
                ) { db in
                    if case .band(let n) = selected { setGain(n, db) }
                }
                .opacity(isBand ? 1 : 0)
                .allowsHitTesting(isBand)

                dialColumn(
                    "Q",
                    value: selectedBand?.q.map { log2($0) },
                    range: log2(EqRange.q.lowerBound)...log2(EqRange.q.upperBound),
                    readout: formatEqQ(selectedBand?.q),
                    rate: 0.2
                ) { octaves in
                    if case .band(let n) = selected { setQ(n, pow(2, octaves)) }
                }
                .opacity(isBand ? 1 : 0)
                .allowsHitTesting(isBand)
            }
        }
        .padding(8)
        .frame(maxHeight: .infinity)
        .background(Color.clmixSurface)
    }

    /// Frequency and Q turn in octaves rather than in Hz: a dial sweeping
    /// 20...20000 linearly would spend nearly all its travel above 1 kHz.
    private func dialColumn(
        _ label: String,
        value: Double?,
        range: ClosedRange<Double>,
        readout: String,
        rate: Double,
        onChange: @escaping (Double) -> Void
    ) -> some View {
        VStack(spacing: 4) {
            Text(label)
                .font(.system(size: 12))
                .foregroundStyle(Color.clmixOnSurfaceVariant)

            DialView(
                value: value ?? range.lowerBound,
                range: range,
                hasValue: value != nil,
                onValueChanged: onChange,
                onTurnFinished: { onChange($0); model.flushEqWrites() },
                rate: rate
            )
            .frame(width: 58, height: 58)

            Text(readout)
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color.clmixReadoutText)
                .lineLimit(1)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
                .background {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.clmixReadoutFill)
                        .overlay(
                            RoundedRectangle(cornerRadius: 6)
                                .stroke(Color.clmixReadoutStroke, lineWidth: 1)
                        )
                }
                .padding(.horizontal, 4)
                .padding(.top, 2)
        }
        .frame(maxWidth: .infinity)
    }

    private func chip(
        _ title: String, lit: Bool, color: Color, litText: Color = .white,
        width: CGFloat? = nil, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: width == nil ? .infinity : nil, maxHeight: .infinity)
                .frame(width: width)
        }
        .foregroundStyle(lit ? litText : Color.clmixOnMuteInactive)
        .background(lit ? color : Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    // MARK: - The selection

    private var isBand: Bool {
        if case .band = selected { return true }
        return false
    }

    private var selectedBand: EqBand? {
        guard case .band(let n) = selected else { return nil }
        return eq.bands.first { $0.band == n }
    }

    private var selectedFilter: EqFilter? {
        switch selected {
        case .loFilter: return eq.loFilter
        case .hiFilter: return eq.hiFilter
        case .band: return nil
        }
    }

    private var selectedFreq: Double? { selectedBand?.freq ?? selectedFilter?.freq }

    private var selectedIsOn: Bool {
        if let band = selectedBand { return band.on == true }
        return selectedFilter?.isIn == true
    }

    private var title: String {
        switch selected {
        case .band(let n): return "\(EqResponse.label(n)) - Band \(n)"
        case .loFilter: return "High-pass filter"
        case .hiFilter: return "Low-pass filter"
        }
    }

    // MARK: - Edits

    private func toggleSelected() {
        switch selected {
        case .band(let n):
            model.editEq(.bandOn(band: n, selectedBand?.on != true), toggle: true)
        case .loFilter:
            model.editEq(.filterIn(lo: true, eq.loFilter.isIn != true), toggle: true)
        case .hiFilter:
            model.editEq(.filterIn(lo: false, eq.hiFilter.isIn != true), toggle: true)
        }
    }

    // Each refuses a value the desk has not reported yet: there is nothing
    // to move from, and a drag would put an invented value on the console.
    private func setFreq(_ target: EqTarget, _ hz: Double) {
        let clamped = min(EqRange.freq.upperBound, max(EqRange.freq.lowerBound, hz))

        switch target {
        case .band(let n):
            guard eq.bands.first(where: { $0.band == n })?.freq != nil else { return }
            model.editEq(.freq(band: n, clamped))
        case .loFilter:
            guard eq.loFilter.freq != nil else { return }
            model.editEq(.filterFreq(lo: true, clamped))
        case .hiFilter:
            guard eq.hiFilter.freq != nil else { return }
            model.editEq(.filterFreq(lo: false, clamped))
        }
    }

    private func setGain(_ band: Int, _ db: Double) {
        guard eq.bands.first(where: { $0.band == band })?.freq != nil else { return }
        model.editEq(.gain(band: band, min(EqRange.gain.upperBound, max(EqRange.gain.lowerBound, db))))
    }

    private func setQ(_ band: Int, _ q: Double) {
        guard eq.bands.first(where: { $0.band == band })?.q != nil else { return }
        model.editEq(.q(band: band, min(EqRange.q.upperBound, max(EqRange.q.lowerBound, q))))
    }
}

/// The EQ screen's picture of the channel: frequency across (20 Hz...20
/// kHz, logarithmic), gain up the side, the combined response drawn over a
/// grid, and a handle for every band and filter the console has reported.
///
/// The handles are the controls. Dragging a band's moves its frequency
/// sideways and its gain up and down; dragging a filter's moves only its
/// frequency. A pinch changes the selected band's Q - spreading widens it.
/// Nothing moves until a touch lands on a handle. Mirrors Android's
/// EqGraphView.
struct EqGraphView: View {
    let eq: ChannelEq
    let selected: EqTarget
    let onSelect: (EqTarget) -> Void
    let onDrag: (EqTarget, Double, Double?) -> Void
    let onPinch: (Int, Double) -> Void
    let onGestureEnd: () -> Void

    @State private var dragging: EqTarget?
    @State private var grabOffset: CGSize = .zero
    @State private var dragStarted = false
    @State private var pinchStartQ: Double?

    private static let minHz = 20.0
    private static let maxHz = 20000.0
    // A little past the bands' ±18, so a band at the limit is still drawn
    // inside the grid rather than on its edge.
    private static let dbSpan = 20.0
    private static let dbLines: [Double] = [-18, -12, -6, 0, 6, 12, 18]
    private static let freqLines: [Double] = [
        30, 40, 50, 60, 70, 80, 90, 100, 200, 300, 400, 500, 600, 700, 800, 900,
        1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000,
    ]
    private static let freqLabels: [(Double, String)] = [
        (50, "50"), (100, "100"), (200, "200"), (500, "500"),
        (1000, "1k"), (2000, "2k"), (5000, "5k"), (10000, "10k"),
    ]
    private static let leftGutter: CGFloat = 30
    private static let bottomGutter: CGFloat = 18
    private static let edge: CGFloat = 6
    private static let touchRadius: CGFloat = 36

    var body: some View {
        GeometryReader { geo in
            let plot = CGRect(
                x: Self.leftGutter, y: Self.edge,
                width: max(1, geo.size.width - Self.leftGutter - Self.edge),
                height: max(1, geo.size.height - Self.edge - Self.bottomGutter)
            )

            ZStack(alignment: .topLeading) {
                Canvas { context, _ in draw(in: &context, plot: plot) }

                ForEach(EqTarget.all, id: \.self) { target in
                    if let point = handle(target, plot: plot) {
                        handleView(target).position(point)
                    }
                }
                .allowsHitTesting(false)
            }
            .contentShape(Rectangle())
            .gesture(drag(plot: plot))
            .simultaneousGesture(pinch)
        }
        .background(Color.clmixSurface)
    }

    // MARK: - Geometry

    private func x(_ hz: Double, _ plot: CGRect) -> CGFloat {
        let f = log10(hz / Self.minHz) / log10(Self.maxHz / Self.minHz)
        return plot.minX + CGFloat(f) * plot.width
    }

    private func hz(_ x: CGFloat, _ plot: CGRect) -> Double {
        let f = Double(min(1, max(0, (x - plot.minX) / plot.width)))
        return Self.minHz * pow(Self.maxHz / Self.minHz, f)
    }

    private func y(_ db: Double, _ plot: CGRect) -> CGFloat {
        let clamped = min(Self.dbSpan, max(-Self.dbSpan, db))
        return plot.minY + CGFloat((Self.dbSpan - clamped) / (2 * Self.dbSpan)) * plot.height
    }

    private func db(_ y: CGFloat, _ plot: CGRect) -> Double {
        let f = Double(min(1, max(0, (y - plot.minY) / plot.height)))
        return min(EqRange.gain.upperBound, max(EqRange.gain.lowerBound, Self.dbSpan - f * 2 * Self.dbSpan))
    }

    /// Where a target's handle sits, or nil if it has nothing to place it by.
    /// A filter's sits on its own curve at the corner, and on the 0 dB line
    /// while it is out.
    private func handle(_ target: EqTarget, plot: CGRect) -> CGPoint? {
        switch target {
        case .band(let n):
            guard let band = eq.bands.first(where: { $0.band == n }), let freq = band.freq else {
                return nil
            }
            return CGPoint(x: x(freq, plot), y: y(band.gain ?? 0, plot))
        case .loFilter:
            guard let freq = eq.loFilter.freq else { return nil }
            return CGPoint(x: x(freq, plot), y: y(EqResponse.filterDb(eq.loFilter, lo: true, hz: freq), plot))
        case .hiFilter:
            guard let freq = eq.hiFilter.freq else { return nil }
            return CGPoint(x: x(freq, plot), y: y(EqResponse.filterDb(eq.hiFilter, lo: false, hz: freq), plot))
        }
    }

    // MARK: - Drawing

    private func draw(in context: inout GraphicsContext, plot: CGRect) {
        let grid = Color.clmixOnSurfaceVariant.opacity(0.2)

        for line in Self.dbLines {
            let ly = y(line, plot)
            var path = Path()
            path.move(to: CGPoint(x: plot.minX, y: ly))
            path.addLine(to: CGPoint(x: plot.maxX, y: ly))
            context.stroke(
                path,
                with: .color(line == 0 ? Color.clmixOnSurfaceVariant.opacity(0.47) : grid),
                lineWidth: line == 0 ? 1.2 : 1
            )

            let label = line > 0 ? "+\(Int(line))" : "\(Int(line))"
            context.draw(
                Text(label).font(.system(size: 10)).foregroundStyle(Color.clmixOnSurfaceVariant),
                at: CGPoint(x: plot.minX - 5, y: ly),
                anchor: .trailing
            )
        }

        for line in Self.freqLines {
            let lx = x(line, plot)
            var path = Path()
            path.move(to: CGPoint(x: lx, y: plot.minY))
            path.addLine(to: CGPoint(x: lx, y: plot.maxY))
            context.stroke(path, with: .color(grid), lineWidth: 1)
        }

        for (freq, label) in Self.freqLabels {
            context.draw(
                Text(label).font(.system(size: 10)).foregroundStyle(Color.clmixOnSurfaceVariant),
                at: CGPoint(x: x(freq, plot), y: plot.maxY + 4),
                anchor: .top
            )
        }

        // The selected band on its own, dashed, so its shape reads apart
        // from the sum of the others.
        if case .band(let n) = selected,
           let band = eq.bands.first(where: { $0.band == n }), band.freq != nil {
            context.stroke(
                trace(plot) { EqResponse.bandDb(band, hz: $0) },
                with: .color(selected.color.opacity(0.8)),
                style: StrokeStyle(lineWidth: 1.5, dash: [5, 4])
            )
        }

        // EQ out: the shape it would have is still drawn, greyed, so the
        // engineer can see what switching it back in would do.
        let eqOut = eq.eqIn == false
        let curve = trace(plot) { EqResponse.totalDb(eq, hz: $0, asIfIn: eqOut) }
        let lineColor = eqOut ? Color.clmixOnSurfaceVariant : Color.clmixPrimary

        var fill = curve
        let zero = y(0, plot)
        fill.addLine(to: CGPoint(x: plot.maxX, y: zero))
        fill.addLine(to: CGPoint(x: plot.minX, y: zero))
        fill.closeSubpath()
        context.fill(fill, with: .color(lineColor.opacity(eqOut ? 0.1 : 0.18)))

        context.stroke(
            curve,
            with: .color(lineColor.opacity(eqOut ? 0.6 : 1)),
            style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round)
        )
    }

    private func trace(_ plot: CGRect, _ response: (Double) -> Double) -> Path {
        var path = Path()
        let steps = max(64, Int(plot.width / 2))

        for i in 0...steps {
            let px = plot.minX + plot.width * CGFloat(i) / CGFloat(steps)
            let point = CGPoint(x: px, y: y(response(hz(px, plot)), plot))
            if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }

        return path
    }

    private func isActive(_ target: EqTarget) -> Bool {
        switch target {
        case .band(let n):
            return eq.eqIn != false && eq.bands.first(where: { $0.band == n })?.on != false
        case .loFilter: return eq.loFilter.isIn == true
        case .hiFilter: return eq.hiFilter.isIn == true
        }
    }

    /// Off reads as an outline only: the handle is still there to grab,
    /// but nothing it stands for is shaping the sound.
    private func handleView(_ target: EqTarget) -> some View {
        let active = isActive(target)
        let isSelected = target == selected
        let size: CGFloat = isSelected ? 28 : 22
        let shape = target == .loFilter || target == .hiFilter
            ? AnyShape(RoundedRectangle(cornerRadius: 3))
            : AnyShape(Circle())

        return ZStack {
            shape.fill(active ? target.color : Color.clmixSurface)
            shape.stroke(isSelected ? Color.clmixOnSurface : target.color, lineWidth: 2)
            Text(target.handleLabel)
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(active ? Color.white : target.color)
        }
        .frame(width: size, height: size)
    }

    // MARK: - Gestures

    private func drag(plot: CGRect) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                if !dragStarted {
                    dragStarted = true
                    guard let hit = nearest(to: value.startLocation, plot: plot),
                          let point = handle(hit, plot: plot) else { return }

                    // Kept so the handle stays where it was under the
                    // finger rather than jumping its centre to the touch.
                    grabOffset = CGSize(
                        width: point.x - value.startLocation.x,
                        height: point.y - value.startLocation.y
                    )
                    dragging = hit
                    onSelect(hit)
                    return
                }

                // A pinch replaces the drag - one finger's wander while the
                // other lands should not drag the band off with it.
                guard let target = dragging, pinchStartQ == nil else { return }

                let hzValue = hz(value.location.x + grabOffset.width, plot)
                let dbValue: Double?
                if case .band = target {
                    dbValue = db(value.location.y + grabOffset.height, plot)
                } else {
                    dbValue = nil
                }
                onDrag(target, hzValue, dbValue)
            }
            .onEnded { _ in
                let wasDragging = dragging != nil
                dragging = nil
                dragStarted = false
                if wasDragging { onGestureEnd() }
            }
    }

    private var pinch: some Gesture {
        MagnifyGesture()
            .onChanged { value in
                guard case .band(let n) = selected,
                      let q = eq.bands.first(where: { $0.band == n })?.q else { return }

                let start = pinchStartQ ?? q
                pinchStartQ = start
                // Spreading the fingers widens the band, which is a lower Q.
                let next = start / Double(max(0.05, value.magnification))
                onPinch(n, min(EqRange.q.upperBound, max(EqRange.q.lowerBound, next)))
            }
            .onEnded { _ in
                pinchStartQ = nil
                onGestureEnd()
            }
    }

    /// The selected handle wins a tie, so a band sitting under another
    /// stays reachable by selecting it from the chips first.
    private func nearest(to location: CGPoint, plot: CGRect) -> EqTarget? {
        var best: EqTarget?
        var bestDistance = Self.touchRadius
        let order = [selected] + EqTarget.all.filter { $0 != selected }

        for target in order {
            guard let point = handle(target, plot: plot) else { continue }
            let distance = hypot(point.x - location.x, point.y - location.y)
            if distance < bestDistance {
                best = target
                bestDistance = distance
            }
        }

        return best
    }
}
