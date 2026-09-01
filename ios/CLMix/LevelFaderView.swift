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
    @State private var dragEndedAt = Date.distantPast

    private let fineSensitivity = 0.2
    private let dragGraceSeconds = 0.3

    var body: some View {
        GeometryReader { geo in
            let height = geo.size.height
            let liveHold = isDragging || Date().timeIntervalSince(dragEndedAt) < dragGraceSeconds
            let fraction = liveHold ? fractionAtDrag : AuxTaper.dbToFraction(db)
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
                        dragEndedAt = Date()
                    }
            )
        }
    }
}
