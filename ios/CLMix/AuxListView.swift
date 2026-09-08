import SwiftUI

/// Mirrors Android's AuxListActivity: a plain title over a list of card
/// rows, one per aux bus. Android reaches this screen's "Log Out" via the
/// system back gesture (there's no visible button - AuxListActivity
/// intercepts back to log out rather than silently leaving a live session
/// behind ConnectActivity); iOS's screen enum swap has no equivalent back
/// gesture to intercept, so a toolbar button gives the same way out.
struct AuxListView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // REMOVE WITH DEMO MODE: back to a plain "Select your Aux".
            Text(model.isDemo ? "Select your Aux (Demo)" : "Select your Aux")
                .font(.system(size: 20, weight: .bold))
                .padding(.horizontal, 20)
                .padding(.top, 24)
                .padding(.bottom, 12)

            ScrollView {
                VStack(spacing: 0) {
                    // Nothing is highlighted here: no mix has been chosen
                    // yet, which is what Android's AuxAdapter.selectedAux
                    // of -1 means on this screen.
                    ForEach(model.auxes) { aux in
                        AuxRow(aux: aux, selected: false) {
                            model.selectAux(aux)
                        }
                    }
                }
                .padding(.vertical, 6)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clmixBackground)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Log Out") { model.logout() }
            }
        }
    }
}

/// One aux bus, on this screen and in the mixer screen's aux sheet alike.
/// Mirrors Android's item_aux.xml + AuxAdapter.onBindViewHolder: filled
/// in the accent rather than merely outlined when it is the live mix,
/// because the sheet is opened to change mix and "which one am I on" has
/// to be answerable at a glance, not by reading every row.
struct AuxRow: View {
    let aux: AuxBus
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(aux.name)
                .font(.system(size: 17))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(18)
        }
        .foregroundStyle(selected ? Color.clmixOnPrimary : Color.clmixOnSurface)
        .background(selected ? Color.clmixPrimary : Color.clmixSurface)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(selected ? Color.clmixPrimary : Color.clmixSurfaceVariant, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
    }
}
