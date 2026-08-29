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
            Text("Select your Aux")
                .font(.system(size: 20, weight: .bold))
                .padding(.horizontal, 20)
                .padding(.top, 24)
                .padding(.bottom, 12)

            ScrollView {
                VStack(spacing: 12) {
                    ForEach(model.auxes) { aux in
                        Button {
                            model.selectAux(aux)
                        } label: {
                            Text(aux.name)
                                .font(.system(size: 17))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(18)
                        }
                        .foregroundStyle(.primary)
                        .background(Color.clmixSurfaceVariant.opacity(0.4))
                        .overlay(
                            RoundedRectangle(cornerRadius: 14)
                                .stroke(Color.clmixSurfaceVariant, lineWidth: 1)
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                }
                .padding(.horizontal, 14)
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
