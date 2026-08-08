import SwiftUI

struct PanSheetView: View {
    let channelName: String
    let initialPan: Double
    let onChange: (Double) -> Void

    @State private var pan: Double

    init(channelName: String, initialPan: Double, onChange: @escaping (Double) -> Void) {
        self.channelName = channelName
        self.initialPan = initialPan
        self.onChange = onChange
        _pan = State(initialValue: initialPan)
    }

    var body: some View {
        VStack(spacing: 16) {
            Capsule()
                .fill(Color.secondary.opacity(0.4))
                .frame(width: 40, height: 4)
                .padding(.top, 8)

            Text(channelName)
                .font(.headline)

            Text(PanFormat.shortLabel(pan))
                .font(.system(size: 34, weight: .bold))
                .foregroundStyle(Color.accentColor)

            CenteredPanSlider(pan: $pan, onChange: onChange)
                .frame(height: 52)
                .padding(.horizontal, 24)

            Button("Center") {
                pan = 0
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
    @Binding var pan: Double
    let onChange: (Double) -> Void

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
                        pan = (fraction * 2) - 1
                        onChange(pan)
                    }
            )
        }
    }
}
