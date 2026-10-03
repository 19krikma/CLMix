import SwiftUI

/// The fork between mixing one aux send and mixing the console itself.
///
/// Only accounts holding Full Mixer Control ever see this - everyone else
/// goes from login straight on to the aux list, which is the flow that
/// existed before this screen. Picking "AUX Only" continues into exactly
/// that same flow, so the two paths differ only in where they start.
///
/// Mirrors Android's ControlChoiceActivity.
struct ControlChoiceView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Choose your control")
                .font(.system(size: 20, weight: .bold))
                .foregroundStyle(Color.clmixOnSurface)
                .padding(.horizontal, 20)
                .padding(.top, 24)
                .padding(.bottom, 12)

            // Deliberately the same size and weight: neither is a
            // default, and picking the wrong one reaches either someone's
            // wedge or the whole room.
            choiceCard(
                title: "AUX Only",
                detail: "Mix one aux send",
                action: { model.chooseAuxOnly() }
            )

            choiceCard(
                title: "Mixer Control",
                // Says plainly that this one is not private to a
                // performer, since that is the whole difference between
                // the two.
                detail: "The console's own faders - heard by everyone",
                action: { model.enterMixerControl() }
            )

            Spacer()

            // Surfaces a rejection that arrived in place of the aux list
            // (a snapshot this account is not scoped to, say) rather than
            // leaving the tap looking like it did nothing.
            if model.statusIsError && !model.statusMessage.isEmpty {
                Text(model.statusMessage)
                    .font(.system(size: 13))
                    .foregroundStyle(Color.clmixMuteActive)
                    .padding(.horizontal, 20)
                    .padding(.bottom, 16)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.clmixBackground.ignoresSafeArea())
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Log Out") { model.logout() }
            }
        }
    }

    private func choiceCard(
        title: String, detail: String, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.system(size: 17))
                    .foregroundStyle(Color.clmixOnSurface)

                Text(detail)
                    .font(.system(size: 13))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(18)
        }
        .background(Color.clmixSurface)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(Color.clmixSurfaceVariant, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
    }
}
