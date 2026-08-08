import SwiftUI

struct MixerView: View {
    @EnvironmentObject var model: AppModel
    let aux: AuxBus

    @State private var selectedBank: String?
    @State private var showAuxSwitcher = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Menu(selectedBank ?? "All") {
                    Button("All") {
                        selectedBank = nil
                        model.selectBank(nil)
                    }
                    ForEach(model.banks, id: \.self) { bank in
                        Button(bank) {
                            selectedBank = bank
                            model.selectBank(bank)
                        }
                    }
                }

                Spacer()

                Button(model.fineMode ? "Fine: On" : "Fine") {
                    model.fineMode.toggle()
                }
                .buttonStyle(.borderedProminent)
                .tint(model.fineMode ? .green : .gray)

                Button("Auxes") { showAuxSwitcher = true }
                    .buttonStyle(.bordered)
            }
            .padding()

            ScrollView(.horizontal) {
                HStack(alignment: .top, spacing: 4) {
                    ForEach(model.channels) { channel in
                        ChannelStripView(channel: channel, fineMode: model.fineMode)
                    }
                }
                .padding()
            }
        }
        .navigationTitle(aux.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Log Out") { model.logout() }
            }
        }
        .sheet(isPresented: $showAuxSwitcher) {
            NavigationStack {
                List(model.auxes) { otherAux in
                    Button(otherAux.name) {
                        showAuxSwitcher = false
                        if otherAux.index != aux.index {
                            model.selectAux(otherAux)
                        }
                    }
                }
                .navigationTitle("Switch Aux")
            }
        }
    }
}
