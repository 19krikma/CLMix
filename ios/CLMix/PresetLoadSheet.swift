import SwiftUI

/// Lists every saved preset (populated via AppModel.requestPresets, fed
/// by mixerDidReceivePresets) and requires picking one, then pressing
/// Load to confirm - unlike the aux list this doesn't apply on tap
/// alone, since loading a preset overwrites every channel's current
/// level+pan on the active aux. Mirrors Android's PresetLoadBottomSheet.
struct PresetLoadSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var selectedName: String?
    @State private var isLoading = false

    var body: some View {
        VStack(spacing: 16) {
            Capsule()
                .fill(Color.secondary.opacity(0.4))
                .frame(width: 40, height: 4)
                .padding(.top, 8)

            Text("Load Preset")
                .font(.headline)

            if model.presetNames.isEmpty {
                Spacer()
                Text("No presets saved yet")
                    .foregroundStyle(.secondary)
                Spacer()
            } else {
                List(model.presetNames, id: \.self, selection: $selectedName) { name in
                    Text(name)
                }
                .listStyle(.plain)
            }

            Button(isLoading ? "Loading..." : "Load") {
                guard let selectedName else { return }
                isLoading = true
                model.loadPreset(name: selectedName) {
                    isLoading = false
                    dismiss()
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(selectedName == nil || isLoading)
            .padding(.horizontal, 24)
            .padding(.bottom, 12)
        }
        .onAppear { model.requestPresets() }
    }
}
