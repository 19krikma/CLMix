import SwiftUI

/// `pan` is live - ChannelStripView passes channel.pan fresh on every
/// server push, not a one-time snapshot - so as long as this view never
/// captures it into @State, the sheet stays live while it's open. Mirrors
/// PanBottomSheet.kt's updateIfIdle on Android (which needs an explicit
/// wiring call from MixerActivity since Android has no equivalent to
/// SwiftUI re-deriving the view from fresh state automatically), including
/// the same drag-grace protection so a stale/in-flight echo of an earlier
/// position can't snap the thumb back right after a drag.
struct PanSheetView: View {
    let channelName: String
    let pan: Double
    let onChange: (Double) -> Void

    @State private var isDragging = false
    @State private var panAtDrag: Double = 0
    @State private var dragEndedAt = Date.distantPast

    private let dragGraceSeconds = 0.3

    private var displayedPan: Double {
        let liveHold = isDragging || Date().timeIntervalSince(dragEndedAt) < dragGraceSeconds
        return liveHold ? panAtDrag : pan
    }

    var body: some View {
        VStack(spacing: 16) {
            Capsule()
                .fill(Color.secondary.opacity(0.4))
                .frame(width: 40, height: 4)
                .padding(.top, 8)

            Text(channelName)
                .font(.headline)

            Text(PanFormat.shortLabel(displayedPan))
                .font(.system(size: 34, weight: .bold))
                .foregroundStyle(Color.accentColor)

            CenteredPanSlider(
                pan: displayedPan,
                onChanged: { newPan in
                    isDragging = true
                    panAtDrag = newPan
                    onChange(newPan)
                },
                onEnded: {
                    isDragging = false
                    dragEndedAt = Date()
                }
            )
            .frame(height: 52)
            .padding(.horizontal, 24)

            Button("Center") {
                panAtDrag = 0
                dragEndedAt = Date()
                onChange(0)
            }
            .buttonStyle(.borderedProminent)
            .tint(.gray)
            .padding(.horizontal, 24)

            Spacer()
        }
    }
}

/// Pan is a balance value, not a fill-from-zero quantity - fills from
/// the track's center outward toward whichever side the thumb has moved
/// to, mirroring PanTrackDrawable.kt on Android.
private struct CenteredPanSlider: View {
    let pan: Double
    let onChanged: (Double) -> Void
    let onEnded: () -> Void

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            let centerX = width / 2
            let thumbX = width * ((pan + 1) / 2)
            let fillWidth = abs(thumbX - centerX)

            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 5)
                    .fill(Color.secondary.opacity(0.25))
                    .frame(height: 10)

                RoundedRectangle(cornerRadius: 5)
                    .fill(Color.accentColor)
                    .frame(width: fillWidth, height: 10)
                    .offset(x: min(centerX, thumbX))

                Circle()
                    .fill(Color.accentColor)
                    .frame(width: 26, height: 26)
                    .offset(x: thumbX - 13)
            }
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        let fraction = min(1, max(0, value.location.x / width))
                        onChanged((fraction * 2) - 1)
                    }
                    .onEnded { _ in onEnded() }
            )
        }
    }
}
