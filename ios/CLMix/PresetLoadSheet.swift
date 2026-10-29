import SwiftUI

/// Lists every preset this account can see - its own, ones shared with
/// it, and public ones (populated via AppModel.requestPresets, fed by
/// mixerDidReceivePresets) - and requires picking one, then pressing
/// Load to confirm - unlike the aux list this doesn't apply on tap
/// alone, since loading a preset overwrites every channel's current
/// level+pan on the active aux. Each row says whose preset it is and who
/// can see it and which aux it was saved from; the account's own carry a
/// button into PresetManageSheet. Presets from every aux are listed, this
/// one's first, and loading one saved from another aux asks first.
/// Mirrors Android's PresetLoadBottomSheet.
struct PresetLoadSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var selectedID: String?
    @State private var managing: Preset?

    private var selected: Preset? {
        model.presets.first { $0.id == selectedID }
    }
    @State private var isLoading = false
    @State private var crossAuxPreset: Preset?

    var body: some View {
        VStack(spacing: 16) {
            Capsule()
                .fill(Color.clmixTrackBackground)
                .frame(width: 40, height: 4)
                .padding(.top, 8)

            VStack(spacing: 4) {
                Text("Load Preset")
                    .font(.system(size: 16, weight: .bold))
                if let aux = model.currentAux {
                    Text("Loads onto \(aux.name)")
                        .font(.system(size: 13))
                        .foregroundStyle(Color.clmixOnSurfaceVariant)
                }
            }

            if model.presets.isEmpty {
                Spacer()
                Text("No presets yet")
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                Spacer()
            } else {
                // Explicit rows rather than List(selection:): a plain
                // List's selection binding only responds to taps while the
                // list is in edit mode on iOS, so tapping a preset never
                // set the selection and Load stayed disabled forever.
                ScrollView {
                    VStack(spacing: 8) {
                        ForEach(model.presets) { preset in
                            row(preset)
                        }
                    }
                    .padding(.horizontal, 24)
                }
            }

            Button {
                guard let selected else { return }
                // nil aux: saved before auxes were recorded, fits any.
                if let aux = selected.aux, aux != model.currentAux?.index {
                    crossAuxPreset = selected
                } else {
                    load(selected)
                }
            } label: {
                Text(isLoading ? "Loading..." : "Load")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            }
            .foregroundStyle(Color.clmixOnPrimary)
            .background(Color.clmixPrimary)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .opacity(selected == nil || isLoading ? 0.5 : 1)
            .disabled(selected == nil || isLoading)
            .padding(.horizontal, 24)
            .padding(.bottom, 12)
        }
        .onAppear { model.requestPresets() }
        .alert(
            "Different aux",
            isPresented: Binding(
                get: { crossAuxPreset != nil },
                set: { if !$0 { crossAuxPreset = nil } }
            ),
            presenting: crossAuxPreset
        ) { preset in
            Button("Cancel", role: .cancel) {}
            Button("Proceed") { load(preset) }
        } message: { preset in
            let here = model.currentAux?.name ?? "this aux"
            let madeFor = preset.auxName ?? "Aux \(preset.aux ?? 0)"
            Text("\"\(preset.name)\" was made for \(madeFor), not \(here). Load it onto \(here) anyway?")
        }
        .sheet(item: $managing) { preset in
            PresetManageSheet(presetID: preset.id)
                .presentationDetents([.medium, .large])
        }
    }

    private func load(_ preset: Preset) {
        isLoading = true
        model.loadPreset(preset) {
            isLoading = false
            dismiss()
        }
    }

    private func row(_ preset: Preset) -> some View {
        let isSelected = preset.id == selectedID

        return HStack(spacing: 0) {
            Button {
                selectedID = preset.id
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(preset.name)
                    // Whose it is and who can see it.
                    Text(preset.subtitle)
                        .font(.system(size: 12))
                        .lineLimit(1)
                        .opacity(0.75)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .contentShape(Rectangle())
            }

            if preset.mine {
                Button {
                    managing = preset
                } label: {
                    Image(systemName: "ellipsis")
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Manage preset")
            }
        }
        .foregroundStyle(isSelected ? Color.clmixOnPrimary : Color.primary)
        .background(isSelected ? Color.clmixPrimary : Color.clear)
        .overlay(
            RoundedRectangle(cornerRadius: 8).stroke(
                isSelected ? Color.clear : Color.clmixOutline,
                lineWidth: 1
            )
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
