import SwiftUI

/// Lists every preset this account can see - its own, ones shared with
/// it, and public ones (populated via AppModel.requestPresets, fed by
/// mixerDidReceivePresets) - and requires picking one, then pressing
/// Load to confirm - unlike the aux list this doesn't apply on tap
/// alone, since loading a preset overwrites every channel's current
/// level+pan on the active aux. Each row says whose preset it is and who
/// can see it; the account's own carry a button into PresetManageSheet.
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

    var body: some View {
        VStack(spacing: 16) {
            Capsule()
                .fill(Color.clmixTrackBackground)
                .frame(width: 40, height: 4)
                .padding(.top, 8)

            VStack(spacing: 4) {
                Text("Load Preset")
                    .font(.system(size: 16, weight: .bold))
                // The server only lists this aux's presets.
                if let aux = model.currentAux {
                    Text("For \(aux.name)")
                        .font(.system(size: 13))
                        .foregroundStyle(Color.clmixOnSurfaceVariant)
                }
            }

            if model.presets.isEmpty {
                Spacer()
                Text("No presets for this aux yet")
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
                isLoading = true
                model.loadPreset(selected) {
                    isLoading = false
                    dismiss()
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
        .sheet(item: $managing) { preset in
            PresetManageSheet(presetID: preset.id)
                .presentationDetents([.medium, .large])
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
