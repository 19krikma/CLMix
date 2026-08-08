import SwiftUI

struct AuxListView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        List(model.auxes) { aux in
            Button(aux.name) {
                model.selectAux(aux)
            }
        }
        .navigationTitle("Aux Buses")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Log Out") { model.logout() }
            }
        }
    }
}
