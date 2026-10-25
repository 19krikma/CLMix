import SwiftUI

/// One input channel's compressor and gate, full screen and on its side.
///
/// The EQ screen's twin (EqView), opened from a strip's COMP or GATE
/// button with that section selected. Opened from COMP it is Dynamics 1
/// alone - compressor, multiband or de-esser, never a gate (captured on
/// channel 43, 2026-10-24) - so there is no COMP/GATE switch and the graph
/// draws the compressor only. Opened from GATE it offers both. Each section has five dials and an
/// IN button; the graph shows the static curve both sections make
/// together, with each threshold a handle to drag. Edits go through
/// AppModel.editDynamics, which moves the screen at once and writes to the
/// desk at a capped rate.
///
/// Only the compressor's ordinary single-band settings and the gate's are
/// offered. When the console reports a section running another mode, the
/// screen says so and shows the first band's settings - it never changes
/// the mode itself. Mirrors Android's DynamicsActivity.
struct DynamicsView: View {
    @EnvironmentObject var model: AppModel

    @State private var compSelected = true

    private var compOnly: Bool { model.dynOpenedOnComp }

    private var dyn: ChannelDynamics {
        model.dynamics ?? ChannelDynamics(channel: model.dynChannel ?? 0)
    }

    private var section: DynSection { compSelected ? dyn.comp : dyn.gate }
    private var params: [DynParam] { compSelected ? DynParam.comp : DynParam.gate }
    private var sectionColor: Color { compSelected ? .clmixEqBandLF : .clmixEqBandHMF }

    private var channelName: String {
        let number = dyn.channel
        return model.channels.first { $0.channel == number }?.name ?? "Ch \(number)"
    }

    var body: some View {
        VStack(spacing: 0) {
            topBar

            HStack(spacing: 8) {
                DynGraphView(
                    dynamics: dyn,
                    compSelected: compSelected,
                    compOnly: compOnly,
                    onSelect: { compSelected = $0 },
                    onThreshold: { comp, db in
                        setValue(comp ? .compThresh : .gateThresh, db)
                    },
                    onGestureEnd: { model.flushDynWrites() }
                )

                controlPanel
                    .frame(width: 330)
            }
            .padding(8)
        }
        .background(Color.clmixBackground.ignoresSafeArea())
        .statusBarHidden(true)
        .persistentSystemOverlays(.hidden)
        .onAppear { compSelected = model.dynOpenedOnComp }
    }

    private var topBar: some View {
        HStack(spacing: 8) {
            Button {
                model.closeDynamics()
            } label: {
                Image(systemName: "chevron.left")
                    .font(.system(size: 18, weight: .semibold))
                    .frame(width: 40, height: 40)
            }
            .accessibilityLabel("Back to the mixer")

            Text("\(dyn.channel)")
                .font(.system(size: 13))
                .foregroundStyle(Color.clmixOnSurfaceVariant)

            Text(channelName)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(Color.clmixOnSurface)
                .lineLimit(1)

            Text("Dynamics")
                .font(.system(size: 15))
                .foregroundStyle(Color.clmixOnSurfaceVariant)

            if model.statusIsError && !model.statusMessage.isEmpty {
                Text(model.statusMessage)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixMuteActive)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.clmixSurface)
    }

    private var controlPanel: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 4) {
                if compOnly {
                    // A heading rather than a switch: Dynamics 1 has no gate.
                    chip("DYNAMICS 1", lit: true, color: .clmixEqBandLF) {}
                        .allowsHitTesting(false)
                        .padding(.trailing, 4)
                } else {
                    chip("COMP", lit: compSelected, color: .clmixEqBandLF) { compSelected = true }
                    chip("GATE", lit: !compSelected, color: .clmixEqBandHMF) { compSelected = false }
                        .padding(.trailing, 4)
                }
                chip("IN", lit: section.isIn == true, color: sectionColor, width: 64) {
                    let target = section.isIn != true
                    model.editDynamics(compSelected ? .compIn(target) : .gateIn(target), toggle: true)
                }
            }
            .frame(height: 36)

            if let text = modeText {
                Text(text)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                    .lineLimit(2)
            }

            Spacer(minLength: 0)

            // Three over two, with an empty slot keeping the bottom row's
            // dials the same width as the top's.
            HStack(alignment: .top, spacing: 0) {
                ForEach(params.prefix(3), id: \.self) { dialColumn($0) }
            }
            HStack(alignment: .top, spacing: 0) {
                ForEach(params.suffix(2), id: \.self) { dialColumn($0) }
                Color.clear.frame(maxWidth: .infinity, maxHeight: 1)
            }
            .padding(.top, 4)
        }
        .padding(8)
        .frame(maxHeight: .infinity)
        .background(Color.clmixSurface)
    }

    /// Names the mode when it is anything but the plain one the dials are
    /// drawn for. The names follow the order the console's own parameter
    /// names list them in (comp-multiband-desser, gate-duck-comp). The
    /// compressor's are confirmed (channel 43, 2026-10-24: 1 multiband, 2
    /// de-esser); the gate slot's are not yet.
    private var modeText: String? {
        guard let mode = section.mode, mode != 0 else { return nil }
        let name: String
        if compSelected {
            name = mode == 1 ? "multiband" : mode == 2 ? "de-esser" : "mode \(mode)"
        } else {
            name = mode == 1 ? "duck" : mode == 2 ? "compressor" : "mode \(mode)"
        }
        return "Console is running this section as \(name) - its first-band "
            + "settings are shown. Change the mode on the desk."
    }

    private func dialColumn(_ param: DynParam) -> some View {
        let value = section.values[param.key]
        let range = param.log
            ? log2(param.range.lowerBound)...log2(param.range.upperBound)
            : param.range

        return VStack(spacing: 2) {
            Text(param.label)
                .font(.system(size: 11))
                .foregroundStyle(Color.clmixOnSurfaceVariant)

            DialView(
                value: value.map { param.log ? log2($0) : $0 } ?? range.lowerBound,
                range: range,
                hasValue: value != nil,
                onValueChanged: { setValue(param, param.log ? pow(2, $0) : $0) },
                onTurnFinished: {
                    setValue(param, param.log ? pow(2, $0) : $0)
                    model.flushDynWrites()
                },
                rate: param.rate
            )
            // Keyed by parameter so switching sections gives each cell a
            // fresh dial rather than one mid-turn on the other section.
            .id(param)
            .frame(width: 48, height: 48)

            Text(param.format(value))
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(Color.clmixReadoutText)
                .lineLimit(1)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 4)
                .background {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.clmixReadoutFill)
                        .overlay(
                            RoundedRectangle(cornerRadius: 6)
                                .stroke(Color.clmixReadoutStroke, lineWidth: 1)
                        )
                }
                .padding(.horizontal, 3)
                .padding(.top, 2)
        }
        .frame(maxWidth: .infinity)
    }

    // Refuses a value the desk has not reported: there is nothing to move
    // from, and a drag would put an invented value on the console.
    private func setValue(_ param: DynParam, _ value: Double) {
        let current = param.isComp ? dyn.comp : dyn.gate
        guard current.values[param.key] != nil else { return }
        let clamped = min(param.range.upperBound, max(param.range.lowerBound, value))
        model.editDynamics(.value(param, clamped))
    }

    private func chip(
        _ title: String, lit: Bool, color: Color, width: CGFloat? = nil,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .lineLimit(1)
                .frame(maxWidth: width == nil ? .infinity : nil, maxHeight: .infinity)
                .frame(width: width)
        }
        .foregroundStyle(lit ? Color.white : Color.clmixOnMuteInactive)
        .background(lit ? color : Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

/// The Dynamics screen's transfer graph: level in across, level out up the
/// side, both -60...0 dB. The diagonal is "nothing happening"; the curve is
/// what the gate and compressor do to it - pulled down by the gate's range
/// below its threshold, bent over by the compressor's ratio above its own,
/// lifted by its makeup gain. A static picture: no knee, attack or
/// release, since those shape time rather than level. The two thresholds
/// are handles. Mirrors Android's DynGraphView.
struct DynGraphView: View {
    let dynamics: ChannelDynamics
    let compSelected: Bool
    /// Dynamics 1 alone - the compressor's curve and handle, no gate.
    let compOnly: Bool
    let onSelect: (Bool) -> Void
    let onThreshold: (Bool, Double) -> Void
    let onGestureEnd: () -> Void

    @State private var dragging: Bool?
    @State private var grabDx: CGFloat = 0
    @State private var started = false

    private static let minDb = -60.0
    private static let grid: [Double] = [-60, -48, -36, -24, -12, 0]
    private static let leftGutter: CGFloat = 30
    private static let bottomGutter: CGFloat = 18
    private static let edge: CGFloat = 6
    private static let touch: CGFloat = 24

    var body: some View {
        GeometryReader { geo in
            // Square, so the unity line runs at 45 degrees and a ratio
            // reads as the angle it is.
            let side = max(1, min(geo.size.width - Self.leftGutter - Self.edge,
                                  geo.size.height - Self.bottomGutter - Self.edge))
            let left = Self.leftGutter + (geo.size.width - Self.leftGutter - Self.edge - side) / 2
            let plot = CGRect(x: left, y: Self.edge, width: side, height: side)

            ZStack(alignment: .topLeading) {
                Canvas { context, _ in draw(in: &context, plot: plot) }

                if !compOnly { handle(comp: !compSelected, plot: plot) }
                handle(comp: compSelected, plot: plot)
            }
            .contentShape(Rectangle())
            .gesture(drag(plot: plot))
        }
        .background(Color.clmixBackground)
    }

    private func x(_ db: Double, _ plot: CGRect) -> CGFloat {
        plot.minX + CGFloat((db - Self.minDb) / -Self.minDb) * plot.width
    }

    private func y(_ db: Double, _ plot: CGRect) -> CGFloat {
        let clamped = min(0, max(Self.minDb, db))
        return plot.maxY - CGFloat((clamped - Self.minDb) / -Self.minDb) * plot.height
    }

    private func db(at px: CGFloat, _ plot: CGRect) -> Double {
        min(0, max(Self.minDb, Self.minDb + Double((px - plot.minX) / plot.width) * -Self.minDb))
    }

    /// Level out for level in, through whichever sections are in.
    private func transfer(_ input: Double) -> Double {
        compOnly ? DynResponse.comp(dynamics.comp, input) : DynResponse.both(dynamics, input)
    }

    private func draw(in context: inout GraphicsContext, plot: CGRect) {
        context.fill(Path(plot), with: .color(Color.clmixSurface))

        let gridColor = Color.clmixOnSurfaceVariant.opacity(0.2)
        for line in Self.grid {
            var h = Path()
            h.move(to: CGPoint(x: plot.minX, y: y(line, plot)))
            h.addLine(to: CGPoint(x: plot.maxX, y: y(line, plot)))
            var v = Path()
            v.move(to: CGPoint(x: x(line, plot), y: plot.minY))
            v.addLine(to: CGPoint(x: x(line, plot), y: plot.maxY))
            context.stroke(h, with: .color(gridColor), lineWidth: 1)
            context.stroke(v, with: .color(gridColor), lineWidth: 1)

            let label = Text("\(Int(line))").font(.system(size: 10))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
            context.draw(label, at: CGPoint(x: plot.minX - 5, y: y(line, plot)), anchor: .trailing)
            context.draw(label, at: CGPoint(x: x(line, plot), y: plot.maxY + 4), anchor: .top)
        }

        var unity = Path()
        unity.move(to: CGPoint(x: plot.minX, y: plot.maxY))
        unity.addLine(to: CGPoint(x: plot.maxX, y: plot.minY))
        context.stroke(unity, with: .color(Color.clmixOnSurfaceVariant.opacity(0.47)),
                       style: StrokeStyle(lineWidth: 1.2, dash: [5, 4]))

        // The thresholds as lines down the whole graph.
        for comp in compOnly ? [true] : [true, false] {
            let section = comp ? dynamics.comp : dynamics.gate
            guard let thresh = section.values["thresh"] else { continue }
            var line = Path()
            line.move(to: CGPoint(x: x(thresh, plot), y: plot.minY))
            line.addLine(to: CGPoint(x: x(thresh, plot), y: plot.maxY))
            let color = comp ? Color.clmixEqBandLF : Color.clmixEqBandHMF
            context.stroke(line, with: .color(color.opacity(comp == compSelected ? 0.8 : 0.35)),
                           style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
        }

        var curve = Path()
        let steps = max(60, Int(plot.width / 2))
        for i in 0...steps {
            let input = Self.minDb + -Self.minDb * Double(i) / Double(steps)
            let point = CGPoint(x: x(input, plot), y: y(transfer(input), plot))
            if i == 0 { curve.move(to: point) } else { curve.addLine(to: point) }
        }

        let anyIn = dynamics.comp.isIn == true || !compOnly && dynamics.gate.isIn == true
        var clipped = context
        clipped.clip(to: Path(plot))
        clipped.stroke(curve, with: .color(anyIn ? Color.clmixPrimary : Color.clmixOnSurfaceVariant.opacity(0.6)),
                       style: StrokeStyle(lineWidth: 2.5, lineJoin: .round))
    }

    @ViewBuilder
    private func handle(comp: Bool, plot: CGRect) -> some View {
        let section = comp ? dynamics.comp : dynamics.gate
        if let thresh = section.values["thresh"] {
            let active = section.isIn == true
            let selected = comp == compSelected
            let color = comp ? Color.clmixEqBandLF : Color.clmixEqBandHMF
            let size: CGFloat = selected ? 28 : 22

            ZStack {
                Circle().fill(active ? color : Color.clmixSurface)
                Circle().stroke(selected ? Color.clmixOnSurface : color, lineWidth: 2)
                Text(comp ? "C" : "G")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(active ? Color.white : color)
            }
            .frame(width: size, height: size)
            .position(x: x(thresh, plot), y: min(plot.maxY, max(plot.minY, y(transfer(thresh), plot))))
            .allowsHitTesting(false)
        }
    }

    private func drag(plot: CGRect) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                if !started {
                    started = true
                    // Thresholds sit on vertical lines, so a touch anywhere
                    // up the line near one grabs it - the selected first.
                    for comp in compOnly ? [true] : [compSelected, !compSelected] {
                        let section = comp ? dynamics.comp : dynamics.gate
                        guard let thresh = section.values["thresh"] else { continue }
                        let hx = x(thresh, plot)
                        if abs(hx - value.startLocation.x) < Self.touch {
                            dragging = comp
                            grabDx = hx - value.startLocation.x
                            onSelect(comp)
                            break
                        }
                    }
                    return
                }

                guard let comp = dragging else { return }
                onThreshold(comp, db(at: value.location.x + grabDx, plot))
            }
            .onEnded { _ in
                if dragging != nil { onGestureEnd() }
                dragging = nil
                started = false
            }
    }
}
