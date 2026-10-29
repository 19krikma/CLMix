import SwiftUI

/// The SOS button: on every screen after login, over everything. Tapping
/// it offers DEAD BATTERY, AUX NOT WORKING, NEED ASSISTANCE or CANCEL; the
/// first three go to the server, which pops an SOS window up on the sound
/// engineer's desktop saying what is needed and who asked. Mirrors
/// Android's Sos.kt.
///
/// Starts in the middle of the top edge and can be dragged anywhere it is
/// less in the way; where it was left is remembered, as a share of the
/// screen, so it lands in the same place on every screen.
///
/// Laid over the app's root in CLMixApp, and again inside the EQ and
/// Dynamics full-screen covers, which sit above the root.
struct SosButton: View {
    @EnvironmentObject private var model: AppModel

    // Wire values, as services/remote_server.py's SOS_REASONS knows them.
    private static let reasons = [
        ("dead_battery", "DEAD BATTERY"),
        ("aux_not_working", "AUX NOT WORKING"),
        ("need_assistance", "NEED ASSISTANCE"),
    ]

    @AppStorage("sosX") private var fx: Double = 0.5
    // Below zero: never moved, so at the top edge.
    @AppStorage("sosY") private var fy: Double = -1

    @State private var dragOffset: CGSize = .zero
    @State private var showOptions = false

    private let size = CGSize(width: 68, height: 36)

    var body: some View {
        GeometryReader { geo in
            let room = CGSize(
                width: max(0, geo.size.width - size.width),
                height: max(0, geo.size.height - size.height)
            )
            let origin = CGPoint(
                x: room.width * fx,
                y: fy < 0 ? 6 : room.height * fy
            )

            ZStack(alignment: .top) {
                Color.clear

                if let notice = model.sosNotice {
                    Text(notice)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Color.clmixOnSurface)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(Color.clmixSurfaceVariant)
                        .clipShape(Capsule())
                        .padding(.top, 52)
                        .padding(.horizontal, 24)
                        .transition(.opacity)
                        .allowsHitTesting(false)
                }

                Text("SOS")
                    .font(.system(size: 14, weight: .bold))
                    .tracking(1.4)
                    .foregroundStyle(.white)
                    .frame(width: size.width, height: size.height)
                    .background(Color(red: 0xC6 / 255, green: 0x28 / 255, blue: 0x28 / 255))
                    .overlay(Capsule().stroke(Color(red: 1, green: 0x8A / 255, blue: 0x80 / 255)))
                    .clipShape(Capsule())
                    .shadow(radius: 4, y: 2)
                    .accessibilityLabel("SOS - call the sound engineer")
                    .position(
                        x: clamp(origin.x + dragOffset.width, 0, room.width) + size.width / 2,
                        y: clamp(origin.y + dragOffset.height, 0, room.height) + size.height / 2
                    )
                    .onTapGesture { showOptions = true }
                    .gesture(
                        DragGesture(minimumDistance: 8)
                            .onChanged { dragOffset = $0.translation }
                            .onEnded { value in
                                let x = clamp(origin.x + value.translation.width, 0, room.width)
                                let y = clamp(origin.y + value.translation.height, 0, room.height)
                                fx = room.width > 0 ? x / room.width : 0.5
                                fy = room.height > 0 ? y / room.height : 0
                                dragOffset = .zero
                            }
                    )
            }
            .animation(.easeInOut(duration: 0.2), value: model.sosNotice)
        }
        .confirmationDialog("SOS - what do you need?", isPresented: $showOptions,
                            titleVisibility: .visible) {
            ForEach(Self.reasons, id: \.0) { item in
                Button(item.1, role: .destructive) { model.sendSos(reason: item.0) }
            }
            Button("CANCEL", role: .cancel) {}
        }
    }

    private func clamp(_ value: CGFloat, _ low: CGFloat, _ high: CGFloat) -> CGFloat {
        min(max(value, low), high)
    }
}
