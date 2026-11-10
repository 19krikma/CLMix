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
                        AuxRow(
                            aux: aux, selected: false,
                            users: model.auxUsers[aux.index] ?? [],
                            me: model.profile?.username
                        ) {
                            model.selectAux(aux)
                        }
                    }
                }
                .padding(.vertical, 6)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clmixBackground.ignoresSafeArea())
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                // An account holding Mixer Control came here from it, and
                // goes back the same way; it logs out from there. Everyone
                // else logged in to this list, so this is their way out.
                if model.mixerControlAllowed {
                    Button("Channels") { model.showChannels() }
                } else {
                    Button("Log Out") { model.logout() }
                }
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
    /// Who has this aux open right now; this account's own shows as
    /// "You", first.
    var users: [AuxUser] = []
    var me: String? = nil
    let action: () -> Void

    /// The sound tech first - if they are in your mix, that is the news -
    /// then yourself, then everyone else in the server's order.
    private var userNames: [String] {
        let tech = users.filter(\.tech).map(\.name)
        let people = users.filter { !$0.tech }
        let mine = people.filter { $0.user == me }.map { _ in "You" }
        return tech + mine + people.filter { $0.user != me }.map(\.name)
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Text(aux.name)
                    .font(.system(size: 17))
                    .frame(maxWidth: .infinity, alignment: .leading)

                if !users.isEmpty {
                    // Capped so a long list never squeezes the aux's own
                    // name out - mirrors Android's aux_users.
                    Label(userNames.joined(separator: ", "), systemImage: "person.fill")
                        .font(.system(size: 13))
                        .lineLimit(2)
                        .multilineTextAlignment(.trailing)
                        .frame(maxWidth: 180, alignment: .trailing)
                        .opacity(0.8)
                        .accessibilityLabel("Active: \(userNames.joined(separator: ", "))")
                }
            }
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
