import SwiftUI

/// Banks as buttons in the pull-down panel, replacing the dropdown that
/// used to sit in the top bar. A dropdown shows one bank and hides the
/// rest behind a tap; on a mixing surface the whole set wants to be
/// reachable in one press, with the live one obvious without opening
/// anything. Mirrors Android's BankAdapter + item_bank.xml.
///
/// Sits in the column rather than floating over the strips: opening it
/// pushes the faders down instead of covering the ones behind it, so
/// nothing is hidden while choosing.
///
/// With [canEdit] (an account holding personalization) a pencil sits
/// bottom right. In edit mode the grid ends in a dashed "+" bank, a bank
/// being named shows as "New bank", and banks are reordered by holding
/// one and dragging it - a line marks the gap it will land in.
struct BankPanelView: View {
    let banks: [String]
    let selectedBank: String?
    let onSelect: (String) -> Void
    var canEdit = false
    var editing = false
    var placeholder = false
    var onToggleEdit: () -> Void = {}
    var onRename: (() -> Void)? = nil
    var onAdd: () -> Void = {}
    var onMove: (Int, Int) -> Void = { _, _ in }

    /// Bank buttons per row. Four keeps a typical name readable at phone
    /// width while still showing a couple of dozen banks without
    /// scrolling.
    private static let columns = 4

    @State private var gridSize: CGSize = .zero
    @State private var dropTarget: Int?
    @State private var dragging: Int?

    var body: some View {
        VStack(spacing: 0) {
            grid
                .padding(.horizontal, 9)
                .padding(.top, 2)
                .padding(.bottom, canEdit ? 0 : 10)

            if canEdit {
                BankEditRow(editing: editing, onRename: onRename, onToggle: onToggleEdit)
            }
        }
        .frame(maxWidth: .infinity)
        // Kept to the panel's own frame. A plain colour background
        // otherwise reaches into any safe area the panel touches - and
        // full screen puts the top bar inside the camera's safe area,
        // so the panel's top edge sits right on it and the fill would
        // paint straight over the bar.
        .background(Color.clmixSurface, ignoresSafeAreaEdges: [])
    }

    private var tileCount: Int {
        banks.count + (editing ? 1 + (placeholder ? 1 : 0) : 0)
    }

    private var grid: some View {
        LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: 0), count: Self.columns),
            spacing: 0
        ) {
            ForEach(Array(banks.enumerated()), id: \.element) { index, bank in
                bankButton(bank, index: index)
            }

            if editing {
                if placeholder {
                    extraTile(Text("New bank").italic(), action: nil)
                }
                extraTile(Image(systemName: "plus").font(.system(size: 20, weight: .bold)), action: onAdd)
                    .accessibilityLabel("Add bank")
            }
        }
        .background(
            GeometryReader { geo in
                Color.clear
                    .onAppear { gridSize = geo.size }
                    .onChange(of: geo.size) { _, size in gridSize = size }
            }
        )
        .overlay(alignment: .topLeading) { dropLine }
        .onDrop(
            of: [.text],
            delegate: InsertionDropDelegate(
                isActive: editing && dragging != nil,
                insertionAt: insertion(at:),
                target: $dropTarget,
                onDrop: { to in
                    if let from = dragging { onMove(from, to) }
                    dragging = nil
                }
            )
        )
    }

    private var rows: Int { max(1, (tileCount + Self.columns - 1) / Self.columns) }
    private var pitch: CGFloat { gridSize.width / CGFloat(Self.columns) }
    private var rowHeight: CGFloat { gridSize.height / CGFloat(rows) }

    /// The gap nearest the finger, counted in banks only - the "+" and
    /// placeholder tiles are not places a bank can go.
    private func insertion(at point: CGPoint) -> Int {
        guard pitch > 0, rowHeight > 0 else { return banks.count }

        let row = min(max(Int(point.y / rowHeight), 0), rows - 1)
        let column = min(max(Int((point.x / pitch).rounded()), 0), Self.columns)

        return min(row * Self.columns + column, banks.count)
    }

    @ViewBuilder
    private var dropLine: some View {
        if let target = dropTarget, pitch > 0 {
            // At the end of a full row the gap is drawn at that row's
            // right edge, not the start of the next.
            let row = target > 0 && target % Self.columns == 0 && target == banks.count
                ? target / Self.columns - 1 : target / Self.columns
            let column = target - row * Self.columns
            let x = min(max(CGFloat(column) * pitch, 2), gridSize.width - 2)

            DropLine(length: rowHeight - 14)
                .offset(x: x - 1.5, y: CGFloat(row) * rowHeight + 7)
        }
    }

    private func bankButton(_ bank: String, index: Int) -> some View {
        let selected = bank == selectedBank

        let button = Button {
            onSelect(bank)
        } label: {
            Text(bank)
                .font(.system(size: 13, weight: .bold))
                .multilineTextAlignment(.center)
                // Two lines whether or not the name needs them, so a row
                // of buttons is the same height regardless of what the
                // console calls its banks - mirrors item_bank's
                // minLines/maxLines pair.
                .lineLimit(2, reservesSpace: true)
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 6)
                .padding(.vertical, 12)
        }
        // Same accent fill the aux sheet uses for the live mix, so "this
        // is the one you are on" looks identical wherever it appears.
        .foregroundStyle(selected ? Color.clmixOnPrimary : Color.clmixOnSurface)
        .background(selected ? Color.clmixPrimary : Color.clmixSurface)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(selected ? Color.clmixPrimary : Color.clmixSurfaceVariant, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .padding(5)

        return Group {
            if editing {
                button.onDrag {
                    dragging = index
                    return reorderItemProvider("bank:\(index)")
                }
            } else {
                button
            }
        }
    }

    private func extraTile<Label: View>(_ label: Label, action: (() -> Void)?) -> some View {
        Button {
            action?()
        } label: {
            label
                .font(.system(size: 13, weight: .bold))
                .lineLimit(1)
                // Two lines of bank name tall, so the row stays level
                // with the banks beside it.
                .frame(maxWidth: .infinity, minHeight: 32)
                .padding(.horizontal, 6)
                .padding(.vertical, 12)
        }
        .foregroundStyle(Color.clmixPrimary)
        .background(AddTileBackground())
        .padding(5)
        .disabled(action == nil)
    }
}
