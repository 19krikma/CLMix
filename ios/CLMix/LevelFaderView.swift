import SwiftUI

/// Vertical level fader. Mirrors the Android app's ChannelAdapter fine-
/// mode touch handling: a normal drag jumps straight to the touch
/// position, but while fineMode is on, the thumb instead moves at
/// fineSensitivity of the finger's own travel distance for finer control
/// than the fader's on-screen travel would otherwise allow.
struct LevelFaderView: View {
    let db: Double
    let fineMode: Bool
    let onChange: (Double) -> Void // reports a new dB value

    @State private var isDragging = false
    @State private var fractionAtDrag: Double = 0
    @State private var lastTranslation: CGFloat = 0
    // True while the thumb stays where the finger left it rather than
    // where the model says - see releaseTask.
    @State private var holding = false
    @State private var releaseTask: Task<Void, Never>?


    private let fineSensitivity = 0.2

    /// How long after letting go the thumb keeps the position the finger
    /// gave it. The console's echo takes a push to come back (~150ms at
    /// best), and without the hold the thumb snaps to the stale dB for a
    /// frame before jumping to where it was actually put.
    private static let dragGraceSeconds: UInt64 = 300_000_000

    // ChannelStripView overlaps the ruler and meter into this view's own
    // frame (its ruler `.padding(.trailing, -5)` and meter
    // `.padding(.leading, -8)`) so they sit close against the track
    // without dead space between them. The hit-test rectangle below
    // excludes exactly those slivers, so a tap that lands on a ruler
    // number or the meter - which only visually overlap this view, not
    // the other way round - never reads as a fader touch.
    private static let rulerOverlap: CGFloat = 5
    private static let meterOverlap: CGFloat = 8

    /// How far the finger has to travel before this becomes a fader
    /// drag at all.
    ///
    /// It used to be zero, which meant the fader claimed the touch the
    /// moment it landed - and since the strips sit in a horizontal
    /// ScrollView, swiping across the grid to reach more channels
    /// dragged whichever fader the swipe started on instead of
    /// scrolling. A gesture that has not started yet is one the scroll
    /// view can take, so this is what lets a sideways swipe scroll.
    private static let dragSlop: CGFloat = 10

    var body: some View {
        GeometryReader { geo in
            let height = geo.size.height
            let fraction = (isDragging || holding)
                ? fractionAtDrag
                : AuxTaper.dbToFraction(db)
            let filledHeight = max(0, CGFloat(fraction) * height)

            ZStack(alignment: .bottom) {
                RoundedRectangle(cornerRadius: 5)
                    .fill(Color.clmixTrackBackground)
                    .frame(width: 10)
                    .frame(maxWidth: .infinity)

                RoundedRectangle(cornerRadius: 5)
                    .fill(Color.clmixPrimary)
                    .frame(width: 10, height: filledHeight)
                    .frame(maxWidth: .infinity, alignment: .bottom)

                Circle()
                    .fill(Color.clmixPrimary)
                    .frame(width: 26, height: 26)
                    .overlay(Circle().stroke(Color.clmixOnPrimary, lineWidth: 2))
                    .offset(y: -filledHeight + 13)
            }
            .contentShape(
                Path(CGRect(
                    x: Self.rulerOverlap,
                    y: 0,
                    width: geo.size.width - Self.rulerOverlap - Self.meterOverlap,
                    height: height
                ))
            )
            // A tap still puts the fader where it was tapped, which the
            // old zero-distance drag did as a side effect of firing on
            // touch-down. Explicit now, because the drag below no
            // longer starts until the finger has actually moved.
            // Fine mode is deliberately left out: there the fader
            // tracks the finger's travel rather than its position, so
            // there is no position for a tap to mean.
            .onTapGesture(coordinateSpace: .local) { location in
                guard !fineMode else { return }

                fractionAtDrag = min(1, max(0, 1 - (location.y / height)))
                onChange(AuxTaper.fractionToDb(fractionAtDrag))
                beginReleaseGrace()
            }
            .gesture(
                DragGesture(minimumDistance: Self.dragSlop)
                    .onChanged { value in
                        if !isDragging {
                            // Sideways means the finger is reaching for
                            // another channel rather than this fader,
                            // so leave it alone. Tested on every event
                            // until the drag starts rather than latched
                            // on the first one: a gesture the scroll
                            // view takes over is cancelled without
                            // onEnded, and a latch would still be set
                            // the next time this fader was touched.
                            guard abs(value.translation.height)
                                >= abs(value.translation.width) else { return }

                            isDragging = true
                            holding = false
                            releaseTask?.cancel()
                            fractionAtDrag = AuxTaper.dbToFraction(db)
                            // The finger has already travelled the slop
                            // by the time this fires. Starting from
                            // where it is now rather than from zero
                            // keeps fine mode's first delta at nothing,
                            // instead of jumping the fader by the slop.
                            lastTranslation = value.translation.height
                        }

                        if fineMode {
                            let deltaY = value.translation.height - lastTranslation
                            lastTranslation = value.translation.height
                            fractionAtDrag = min(
                                1, max(0, fractionAtDrag - (deltaY / height) * fineSensitivity)
                            )
                        } else {
                            fractionAtDrag = min(1, max(0, 1 - (value.location.y / height)))
                        }

                        onChange(AuxTaper.fractionToDb(fractionAtDrag))
                    }
                    .onEnded { _ in
                        // Nothing to let go of if this drag never
                        // became a fader move.
                        guard isDragging else { return }

                        isDragging = false
                        beginReleaseGrace()
                    }
            )
        }
    }

    /// Keeps the thumb where the finger left it for a moment.
    ///
    /// Timed explicitly rather than by comparing a stored release time
    /// against Date() inside the body: reading the clock while building
    /// a view makes what it draws depend on when it happened to be
    /// built, and nothing would re-render it when the grace actually
    /// expired - the thumb only came unstuck because the next levels
    /// push rebuilt the strip for its own reasons.
    private func beginReleaseGrace() {
        holding = true
        releaseTask?.cancel()
        // @MainActor explicitly: a gesture callback carries no actor
        // isolation of its own, so a bare Task would land on the global
        // executor and write @State off the main thread.
        releaseTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: Self.dragGraceSeconds)
            guard !Task.isCancelled else { return }
            holding = false
        }
    }
}
