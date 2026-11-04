import SwiftUI
import UniformTypeIdentifiers

/// The pieces of bank edit mode shared by the bank panel and the strips:
/// the drop handling that works out which gap a drag is over, the line
/// drawn in that gap, the pencil/done button, and the channel picker.
/// Mirrors Android's ReorderDrag and BankChannelsBottomSheet.

/// Drop handling for a whole row or grid at once. [insertionAt] turns a
/// point in the container's own coordinates into an insertion point (0 is
/// before the first item, count is after the last); the container draws
/// its line wherever [target] says, and nothing moves until the drop.
struct InsertionDropDelegate: DropDelegate {
    let isActive: Bool
    let insertionAt: (CGPoint) -> Int
    @Binding var target: Int?
    let onDrop: (Int) -> Void

    func validateDrop(info: DropInfo) -> Bool { isActive }

    func dropEntered(info: DropInfo) {
        target = insertionAt(info.location)
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        guard isActive else { return nil }
        let index = insertionAt(info.location)
        if index != target { target = index }
        return DropProposal(operation: .move)
    }

    func dropExited(info: DropInfo) {
        target = nil
    }

    func performDrop(info: DropInfo) -> Bool {
        let index = insertionAt(info.location)
        target = nil
        onDrop(index)
        return true
    }
}

/// The drop line itself: the accent colour, rounded, a few points thick.
struct DropLine: View {
    var vertical = true
    var length: CGFloat

    var body: some View {
        Capsule()
            .fill(Color.clmixPrimary)
            .frame(width: vertical ? 3 : length, height: vertical ? length : 3)
            .allowsHitTesting(false)
    }
}

/// What a drag carries. The payload is never read back - the source is
/// tracked by the view that started it - but a drag needs an item.
func reorderItemProvider(_ tag: String) -> NSItemProvider {
    NSItemProvider(object: tag as NSString)
}

/// The pencil that turns edit mode on, and the filled tick that turns it
/// off again. Sits bottom right of the bank panel.
struct BankEditRow: View {
    let editing: Bool
    /// Landscape only: the "+" strip sits at the left of the strips,
    /// right where the side panel covers it, so the panel carries its own
    /// way to the channel list. Shown in place of the hint.
    var onAddChannels: (() -> Void)? = nil
    /// Renames the selected bank. Shown beside the tick while editing,
    /// when there is a bank of this account's own to rename.
    var onRename: (() -> Void)? = nil
    let onToggle: () -> Void
    /// Beside the banks in the tablet top bar rather than under them: the
    /// hint only takes room while editing, and there is no row to pad.
    var compact = false

    var body: some View {
        HStack {
            if editing, let onAddChannels {
                Button(action: onAddChannels) {
                    Text("+ Channels")
                        .font(.system(size: 13, weight: .semibold))
                        .padding(.horizontal, 14)
                        .frame(height: 36)
                }
                .foregroundStyle(Color.clmixOnSurface)
                .background(Color.clmixSurfaceVariant)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            } else if !compact || editing {
                Text("Hold and drag to reorder")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                    .opacity(editing ? 1 : 0)
            }

            if !compact { Spacer() }

            if editing, let onRename {
                Button(action: onRename) {
                    Text("Rename")
                        .font(.system(size: 13, weight: .semibold))
                        .padding(.horizontal, 14)
                        .frame(height: 36)
                }
                .foregroundStyle(Color.clmixOnSurface)
                .background(Color.clmixSurfaceVariant)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }

            Button(action: onToggle) {
                Image(systemName: editing ? "checkmark" : "pencil")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(editing ? Color.clmixOnPrimary : Color.clmixOnSurface)
                    .frame(width: 36, height: 36)
                    .background(Circle().fill(editing ? Color.clmixPrimary : Color.clear))
            }
            .accessibilityLabel(editing ? "Done editing banks" : "Edit banks")
        }
        .padding(.leading, compact ? 0 : 14)
        .padding(.trailing, compact ? 6 : 14)
        .padding(.bottom, compact ? 0 : 6)
    }
}

/// The dashed outline the "+" tiles share: the shape of what they add,
/// drawn as an empty slot.
struct AddTileBackground: View {
    var body: some View {
        RoundedRectangle(cornerRadius: 12)
            .strokeBorder(Color.clmixPrimary, style: StrokeStyle(lineWidth: 1.5, dash: [6, 4]))
    }
}

/// The "+" strip ahead of the channels in edit mode - shaped like a
/// channel strip, and pinned rather than scrolled so it is always there.
struct AddChannelTile: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Image(systemName: "plus")
                    .font(.system(size: 26, weight: .semibold))
                Text("Add")
                    .font(.system(size: 12, weight: .bold))
            }
            .foregroundStyle(Color.clmixPrimary)
            .frame(width: 68)
            .frame(maxHeight: .infinity)
            .background(AddTileBackground())
            .contentShape(Rectangle())
        }
        .padding(.vertical, 10)
        .padding(.leading, 10)
        .accessibilityLabel("Add channels to this bank")
    }
}

/// Every channel on the desk with a checkbox, ticked for the ones in the
/// bank. Each tick is a toggle that applies straight away, so the strips
/// behind change as you go; Done only closes it. Listed in the console's
/// order - this is for finding a channel, and the desk's numbering is how
/// you find one.
struct BankChannelsSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let bankName: String

    private var inBank: Set<Int> {
        Set(model.customBanks.first(where: { $0.name == bankName })?.channels ?? [])
    }

    var body: some View {
        let ticked = inBank

        VStack(spacing: 0) {
            Text("Channels in \"\(bankName)\"")
                .font(.system(size: 16, weight: .bold))
                .padding(.top, 20)
            Text("\(ticked.count) of \(model.bankChannels.count) channels")
                .font(.system(size: 13))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
                .padding(.top, 4)
                .padding(.bottom, 8)

            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(model.bankChannels) { channel in
                        let on = ticked.contains(channel.channel)

                        Button {
                            model.setChannel(channel.channel, inBank: bankName, included: !on)
                        } label: {
                            HStack(spacing: 14) {
                                Image(systemName: on ? "checkmark.square.fill" : "square")
                                    .font(.system(size: 22))
                                    .foregroundStyle(on ? Color.clmixPrimary : Color.clmixOnSurfaceVariant)
                                Text("\(channel.channel)")
                                    .font(.system(size: 15).monospacedDigit())
                                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                                    .frame(minWidth: 28, alignment: .leading)
                                Text(channel.name)
                                    .font(.system(size: 15))
                                    .foregroundStyle(Color.clmixOnSurface)
                                Spacer()
                            }
                            .frame(minHeight: 48)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .padding(.horizontal, 20)
                    }
                }
            }

            Button {
                dismiss()
            } label: {
                Text("Done")
                    .font(.system(size: 15, weight: .semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 46)
            }
            .foregroundStyle(Color.clmixOnPrimary)
            .background(Color.clmixPrimary)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal, 24)
            .padding(.vertical, 12)
        }
        .background(Color.clmixSurface.ignoresSafeArea())
    }
}
