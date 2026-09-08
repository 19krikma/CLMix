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
struct BankPanelView: View {
    let banks: [String]
    let selectedBank: String?
    let onSelect: (String) -> Void

    /// Bank buttons per row. Four keeps a typical name readable at phone
    /// width while still showing a couple of dozen banks without
    /// scrolling.
    private static let columns = 4

    var body: some View {
        LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: 0), count: Self.columns),
            spacing: 0
        ) {
            ForEach(banks, id: \.self) { bank in
                bankButton(bank)
            }
        }
        .padding(.horizontal, 9)
        .padding(.top, 2)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity)
        .background(Color.clmixSurface)
    }

    private func bankButton(_ bank: String) -> some View {
        let selected = bank == selectedBank

        return Button {
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
    }
}
