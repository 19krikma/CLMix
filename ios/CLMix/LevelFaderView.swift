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
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        if !isDragging {
                            isDragging = true
                            holding = false
                            releaseTask?.cancel()
                            fractionAtDrag = AuxTaper.dbToFraction(db)
                            lastTranslation = 0
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
                        isDragging = false

                        // Timed explicitly rather than by comparing a
                        // stored release time against Date() inside the
                        // body: reading the clock while building a view
                        // makes what it draws depend on when it happened
                        // to be built, and nothing would re-render it
                        // when the grace actually expired - the thumb
                        // only came unstuck because the next levels push
                        // rebuilt the strip for its own reasons.
                        holding = true
                        releaseTask?.cancel()
                        // @MainActor explicitly: a gesture callback
                        // carries no actor isolation of its own, so a
                        // bare Task would land on the global executor
                        // and write @State off the main thread.
                        releaseTask = Task { @MainActor in
                            try? await Task.sleep(nanoseconds: Self.dragGraceSeconds)
                            guard !Task.isCancelled else { return }
                            holding = false
                        }
                    }
            )
        }
    }
}
