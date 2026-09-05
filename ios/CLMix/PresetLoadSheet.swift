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
                .fill(Color.clmixTrackBackground)
                .frame(width: 40, height: 4)
                .padding(.top, 8)

            Text("Load Preset")
                .font(.system(size: 16, weight: .bold))

            if model.presetNames.isEmpty {
                Spacer()
                Text("No presets saved yet")
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                Spacer()
            } else {
                // Explicit rows rather than List(selection:): a plain
                // List's selection binding only responds to taps while the
                // list is in edit mode on iOS, so tapping a preset never
                // set selectedName and Load stayed disabled forever.
                ScrollView {
                    VStack(spacing: 8) {
                        ForEach(model.presetNames, id: \.self) { name in
                            Button {
                                selectedName = name
                            } label: {
                                Text(name)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.horizontal, 14)
                                    .frame(height: 48)
                            }
                            .foregroundStyle(
                                selectedName == name ? Color.clmixOnPrimary : Color.primary
                            )
                            .background(selectedName == name ? Color.clmixPrimary : Color.clear)
                            .overlay(
                                RoundedRectangle(cornerRadius: 8).stroke(
                                    selectedName == name ? Color.clear : Color.clmixOutline,
                                    lineWidth: 1
                                )
                            )
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }
                    .padding(.horizontal, 24)
                }
            }

            Button {
                guard let selectedName else { return }
                isLoading = true
                model.loadPreset(name: selectedName) {
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
            .opacity(selectedName == nil || isLoading ? 0.5 : 1)
            .disabled(selectedName == nil || isLoading)
            .padding(.horizontal, 24)
            .padding(.bottom, 12)
        }
        .onAppear { model.requestPresets() }
    }
}
