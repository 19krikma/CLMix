import SwiftUI

/// Mixer Control's bank picker: every bank on the console's surface,
/// layer by layer, the way the desk itself lays them out.
///
/// Each layer is a labelled row of its banks, four to a row, which is
/// what a layer holds on the desk. On a phone the label sits above its
/// row; `inline` puts it at the start of the row instead, which keeps the
/// iPad's top bar, where this lives there, from growing a line per layer.
///
/// A bank with no input channels on it - DCAs, outputs - is shown dimmed
/// and does nothing when tapped: it is on the surface, so it is listed,
/// but Mixer Control only drives input channels and has no strips to show
/// for it. Mirrors Android's LayeredBankPanel.
struct LayeredBankPanelView: View {
    let layers: [BankLayer]
    let selectedId: String?
    var inline = false
    let onPick: (LayoutBank) -> Void

    private static let perRow = 4
    private static let sideLabelWidth: CGFloat = 52

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(layers) { layer in
                let label = layer.number > 0 ? "LAYER \(layer.number)" : nil
                let rows = stride(from: 0, to: layer.banks.count, by: Self.perRow).map {
                    Array(layer.banks[$0..<min($0 + Self.perRow, layer.banks.count)])
                }

                ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                    if !inline, index == 0, let label {
                        Text(label)
                            .font(.system(size: 11, weight: .bold))
                            .tracking(0.9)
                            .foregroundStyle(Color.clmixOnSurfaceVariant)
                            .padding(.horizontal, 6)
                            .padding(.top, 8)
                            .padding(.bottom, 2)
                    }

                    HStack(spacing: 0) {
                        if inline, label != nil {
                            // Every row of the layer keeps the label's
                            // width, so the banks line up under each other.
                            Text(index == 0 ? (label ?? "").replacingOccurrences(of: " ", with: "\n") : "")
                                .font(.system(size: 10, weight: .bold))
                                .tracking(0.8)
                                .multilineTextAlignment(.center)
                                .foregroundStyle(Color.clmixOnSurfaceVariant)
                                .frame(width: Self.sideLabelWidth)
                        }

                        ForEach(row) { bank in
                            bankButton(bank)
                        }

                        // Short rows keep the same button width as full ones.
                        ForEach(0..<(Self.perRow - row.count), id: \.self) { _ in
                            Color.clear.frame(maxWidth: .infinity, maxHeight: 1)
                        }
                    }
                }
            }
        }
        .padding(.horizontal, inline ? 4 : 9)
        .padding(.top, inline ? 4 : 2)
        .padding(.bottom, inline ? 4 : 10)
        // The panel floats over the strips on a phone, so a tap on it
        // that no bank takes - a dimmed one, a label, a gap - stops here
        // rather than landing on the fader underneath.
        .contentShape(Rectangle())
        .onTapGesture {}
    }

    /// The same look as the aux screen's bank buttons (BankPanelView).
    private func bankButton(_ bank: LayoutBank) -> some View {
        let selected = bank.id == selectedId
        let usable = bank.inputs > 0

        return Button {
            if usable { onPick(bank) }
        } label: {
            Text(bank.name)
                .font(.system(size: 13, weight: .bold))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .frame(maxWidth: .infinity, minHeight: 40)
                .padding(.horizontal, 6)
                .padding(.vertical, 6)
                .foregroundStyle(selected ? Color.clmixOnPrimary : Color.clmixOnSurface)
                .background(
                    RoundedRectangle(cornerRadius: 12)
                        .fill(selected ? Color.clmixPrimary : Color.clmixSurface)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(selected ? Color.clmixPrimary : Color.clmixSurfaceVariant, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
        .opacity(usable ? 1 : 0.4)
        .disabled(!usable)
        .accessibilityLabel(usable ? bank.name : "\(bank.name), no input channels")
        .padding(5)
    }
}
