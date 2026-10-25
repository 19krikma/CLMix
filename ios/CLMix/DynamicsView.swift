import SwiftUI

/// One input channel's Dynamics 1 or Dynamics 2, full screen and on its
/// side.
///
/// The EQ screen's twin (EqView), opened from a strip's COMP button for
/// Dynamics 1 or its GATE button for Dynamics 2. Edits go through
/// AppModel.editDynamics, which moves the screen at once and writes to the
/// console at a capped rate.
///
/// Either way the screen shows that one section, headed with the mode the
/// console is running it in, and that mode's controls. Dynamics 1
/// (DynMode.pages): the compressor's five dials; multiband's LOW, MID and
/// HIGH bands, each with its own power button, and ALL for the overall
/// threshold, gain and crossovers; or the de-esser's band with its
/// frequency and width. Dynamics 2 (GateMode.pages): gate and ducker share
/// threshold, range, attack, hold and release; the compressor has
/// threshold, ratio, attack, release and gain. The mode itself is never
/// changed here. Mirrors Android's DynamicsActivity.
struct DynamicsView: View {
    @EnvironmentObject var model: AppModel

    @State private var page = 0

    /// Dynamics 1 (true) or 2, fixed by the button that opened the screen.
    private var compSelected: Bool { model.dynOpenedOnComp }

    /// Each section's mode as the console reports it.
    private var mode: DynMode { DynMode(rawValue: dyn.comp.mode ?? 0) ?? .compressor }
    private var gateMode: GateMode { GateMode(rawValue: dyn.gate.mode ?? 0) ?? .gate }

    private var pages: [DynMode.Page] { compSelected ? mode.pages : gateMode.pages }

    private var shownPage: DynMode.Page { pages[min(page, pages.count - 1)] }

    /// What the graph draws: the shown band in place of the compressor.
    private var graphDyn: ChannelDynamics {
        guard compSelected else { return dyn }
        var out = dyn
        out.comp = dyn.comp.band(shownPage.graphBand)
        return out
    }

    private var heading: String {
        if section.mode == nil { return compSelected ? "DYNAMICS 1" : "DYNAMICS 2" }
        return compSelected ? mode.title : gateMode.title
    }

    private var dyn: ChannelDynamics {
        model.dynamics ?? ChannelDynamics(channel: model.dynChannel ?? 0)
    }

    private var section: DynSection { compSelected ? dyn.comp : dyn.gate }
    private var params: [DynParam] { shownPage.params }
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
                    dynamics: graphDyn,
                    compSelected: compSelected,
                    onThreshold: { comp, db in
                        setValue(comp ? DynParam.band(shownPage.graphBand)[0] : .gateThresh, db)
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
        .onChange(of: section.mode) { _, _ in page = 0 }
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
                // A heading naming the mode rather than a switch: each
                // screen is one section.
                chip(heading, lit: true, color: sectionColor) {}
                    .allowsHitTesting(false)
                    .padding(.trailing, 4)
                chip("", icon: "power", lit: section.isIn == true, color: sectionColor, width: 64) {
                    let target = section.isIn != true
                    model.editDynamics(compSelected ? .compIn(target) : .gateIn(target), toggle: true)
                }
                .accessibilityLabel("Section in or out")
            }
            .frame(height: 36)

            if pages.count > 1 {
                HStack(spacing: 4) {
                    ForEach(Array(pages.enumerated()), id: \.offset) { index, p in
                        chip(p.tab ?? "", lit: index == min(page, pages.count - 1),
                             color: sectionColor) { page = index }
                    }

                    if let band = shownPage.band {
                        chip("", icon: "power", lit: section.values["band_in_\(band)"] == 1,
                             color: .clmixEqBandLF, width: 64) {
                            let target = section.values["band_in_\(band)"] != 1
                            model.editDynamics(.bandIn(band, target), toggle: true)
                        }
                        .padding(.leading, 4)
                        .accessibilityLabel("Band in or out")
                    } else {
                        Color.clear.frame(width: 68)
                    }
                }
                .frame(height: 32)
            }

            Spacer(minLength: 0)

            // Three over three; a page with fewer dials pads the rest so
            // every dial keeps the same width.
            HStack(alignment: .top, spacing: 0) {
                ForEach(params.prefix(3), id: \.self) { dialColumn($0) }
                ForEach(0..<max(0, 3 - params.count), id: \.self) { _ in
                    Color.clear.frame(maxWidth: .infinity, maxHeight: 1)
                }
            }
            HStack(alignment: .top, spacing: 0) {
                ForEach(Array(params.dropFirst(3)), id: \.self) { dialColumn($0) }
                ForEach(0..<max(0, 3 - max(0, params.count - 3)), id: \.self) { _ in
                    Color.clear.frame(maxWidth: .infinity, maxHeight: 1)
                }
            }
            .padding(.top, 4)
        }
        .padding(8)
        .frame(maxHeight: .infinity)
        .background(Color.clmixSurface)
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
        _ title: String, icon: String? = nil, lit: Bool, color: Color, width: CGFloat? = nil,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Group {
                if let icon {
                    Image(systemName: icon).font(.system(size: 16, weight: .semibold))
                } else {
                    Text(title)
                        .font(.system(size: 12, weight: .semibold))
                        .lineLimit(1)
                }
            }
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
/// what the open section does to it - Dynamics 1 (compSelected) or
/// Dynamics 2 in whichever mode it runs (DynResponse). A static picture:
/// no knee, attack or release, since those shape time rather than level.
/// The threshold is a handle. Mirrors Android's DynGraphView.
struct DynGraphView: View {
    let dynamics: ChannelDynamics
    /// True for Dynamics 1, false for Dynamics 2.
    let compSelected: Bool
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

    /// Level out for level in, through the open section.
    private func transfer(_ input: Double) -> Double {
        compSelected ? DynResponse.comp(dynamics.comp, input) : DynResponse.gate(dynamics.gate, input)
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

        // The threshold as a line down the whole graph.
        let section = compSelected ? dynamics.comp : dynamics.gate
        if let thresh = section.values["thresh"] {
            var line = Path()
            line.move(to: CGPoint(x: x(thresh, plot), y: plot.minY))
            line.addLine(to: CGPoint(x: x(thresh, plot), y: plot.maxY))
            let color = compSelected ? Color.clmixEqBandLF : Color.clmixEqBandHMF
            context.stroke(line, with: .color(color.opacity(0.8)),
                           style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
        }

        var curve = Path()
        let steps = max(60, Int(plot.width / 2))
        for i in 0...steps {
            let input = Self.minDb + -Self.minDb * Double(i) / Double(steps)
            let point = CGPoint(x: x(input, plot), y: y(transfer(input), plot))
            if i == 0 { curve.move(to: point) } else { curve.addLine(to: point) }
        }

        let anyIn = section.isIn == true
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
            let color = comp ? Color.clmixEqBandLF : Color.clmixEqBandHMF
            let size: CGFloat = 28
            let label = comp ? "C" : (GateMode(rawValue: section.mode ?? 0) ?? .gate).handle

            ZStack {
                Circle().fill(active ? color : Color.clmixSurface)
                Circle().stroke(Color.clmixOnSurface, lineWidth: 2)
                Text(label)
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
                    // The threshold sits on a vertical line, so a touch
                    // anywhere up the line near it grabs it.
                    let section = compSelected ? dynamics.comp : dynamics.gate
                    if let thresh = section.values["thresh"] {
                        let hx = x(thresh, plot)
                        if abs(hx - value.startLocation.x) < Self.touch {
                            dragging = compSelected
                            grabDx = hx - value.startLocation.x
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
