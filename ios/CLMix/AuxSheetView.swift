import SwiftUI

/// The aux picker, at the bottom of the mixer screen within thumb reach.
/// Mirrors Android's persistent BottomSheetBehavior sheet in
/// activity_mixer.xml: the collapsed state *is* the label naming the live
/// mix, so the same strip of screen both answers "which mix is this?" and
/// opens the list - by a tap or by dragging it up.
///
/// It floats over the strips rather than pushing them, so the faders keep
/// their positions and their height while choosing. Never dismissable:
/// collapsed, this is the label naming the live mix, so there is no state
/// where it should not be on screen.
struct AuxSheetView: View {
    let auxes: [AuxBus]
    let currentIndex: Int
    let onSelect: (AuxBus) -> Void

    /// Collapsed height: tall enough to read the aux name and hit the
    /// arrows, short enough not to eat fader travel. Mirrors Android's
    /// `aux_sheet_peek` dimen, and the strips reserve exactly this much
    /// at their bottom so a Mute button can never end up underneath it.
    static let peekHeight: CGFloat = 52

    /// How much of the screen the expanded sheet covers. Enough to show a
    /// handful of mixes at once, short of swallowing the whole display -
    /// the strips behind it stay partly visible, which is the point of it
    /// floating over them rather than replacing them.
    private static let screenFraction: CGFloat = 0.55

    @State private var expanded = false
    @State private var dragOffset: CGFloat = 0
    // Guards the scroll-to-selection so it runs once per opening rather
    // than on every frame of the drag, which would fight the finger.
    @State private var scrolledForThisOpening = false
    @State private var scrollTrigger = 0

    var body: some View {
        GeometryReader { geo in
            let expandedHeight = max(Self.peekHeight, geo.size.height * Self.screenFraction)
            let travel = expandedHeight - Self.peekHeight
            let offset = min(travel, max(0, (expanded ? 0 : travel) + dragOffset))
            let progress = travel == 0 ? 0 : 1 - offset / travel

            VStack(spacing: 0) {
                handle(progress: progress, travel: travel)
                auxList
            }
            .frame(height: expandedHeight, alignment: .top)
            .background(Color.clmixSurface)
            .shadow(color: .black.opacity(0.25), radius: 12, y: -2)
            .frame(maxHeight: .infinity, alignment: .bottom)
            .offset(y: offset)
        }
    }

    /// Arrows either side of the live aux name. Both point the way the
    /// sheet will travel, so they flip when it is open, and they turn
    /// with the drag rather than snapping at the end. Grey rather than
    /// accent so the name stays the thing the eye lands on.
    private func handle(progress: CGFloat, travel: CGFloat) -> some View {
        HStack(spacing: 0) {
            arrow(progress: progress)

            Text(currentAuxName)
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(Color.clmixPrimary)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity)

            arrow(progress: progress)
        }
        .padding(.horizontal, 12)
        .frame(height: Self.peekHeight)
        .contentShape(Rectangle())
        .accessibilityLabel(expanded ? "Hide aux list" : "Show aux list")
        .onTapGesture { setExpanded(!expanded) }
        // minimumDistance so a tap still reads as a tap rather than a
        // zero-length drag - the tap is the part BottomSheetBehavior does
        // not provide on Android either.
        .gesture(
            DragGesture(minimumDistance: 8)
                .onChanged { value in
                    dragOffset = value.translation.height

                    // The moment it starts to move, so the list is
                    // already in the right place by the time it is
                    // readable rather than jumping once it settles.
                    let moved = min(travel, max(0, (expanded ? 0 : travel) + dragOffset))
                    if moved < travel && !scrolledForThisOpening {
                        scrolledForThisOpening = true
                        scrollTrigger += 1
                    } else if moved >= travel {
                        scrolledForThisOpening = false
                    }
                }
                .onEnded { value in
                    // Where the finger was heading, not just where it
                    // let go - a short flick opens the sheet rather than
                    // dropping it back.
                    let projected = (expanded ? 0 : travel) + value.predictedEndTranslation.height
                    setExpanded(projected < travel / 2)
                }
        )
    }

    private var currentAuxName: String {
        auxes.first(where: { $0.index == currentIndex })?.name ?? ""
    }

    private func arrow(progress: CGFloat) -> some View {
        Image(systemName: "chevron.up")
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(Color.clmixOnSurfaceVariant)
            .frame(width: 26, height: 26)
            .rotationEffect(.degrees(Double(progress) * 180))
    }

    private var auxList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(auxes) { aux in
                        AuxRow(aux: aux, selected: aux.index == currentIndex) {
                            setExpanded(false)
                            onSelect(aux)
                        }
                        .id(aux.index)
                    }
                }
                .padding(.vertical, 6)
                .padding(.bottom, 10)
            }
            .onChange(of: scrollTrigger) { _, _ in
                scrollToSelection(proxy)
            }
        }
    }

    /// Brings the live mix into view before the sheet arrives at it.
    ///
    /// With a handful of auxes everything fits and this does nothing. On
    /// a console with thirty, opening the sheet would otherwise land on
    /// the top of the list with the mix you are actually on somewhere
    /// below the fold - so the first thing you would do every time is
    /// scroll to find it.
    private func scrollToSelection(_ proxy: ScrollViewProxy) {
        guard let index = auxes.firstIndex(where: { $0.index == currentIndex }) else { return }

        // One row above the selection where there is room, so it arrives
        // with some context rather than jammed against the top edge.
        proxy.scrollTo(auxes[max(0, index - 1)].index, anchor: .top)
    }

    private func setExpanded(_ value: Bool) {
        if value && !scrolledForThisOpening {
            scrolledForThisOpening = true
            scrollTrigger += 1
        }

        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
            expanded = value
            dragOffset = 0
        }

        if !value {
            scrolledForThisOpening = false
        }
    }
}
