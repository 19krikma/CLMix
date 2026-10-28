import SwiftUI

/// One of this account's own presets: whether it is public, which other
/// accounts it is shared with, and deleting it. Every change goes to the
/// server straight away - there is no Save - and the server answers with
/// a fresh presets list, so this reads the preset back off the model by
/// id and always shows what the server has rather than what was tapped.
/// Mirrors Android's PresetManageBottomSheet.
struct PresetManageSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    let presetID: String

    @State private var confirmingDelete = false

    private var preset: Preset? {
        model.presets.first { $0.id == presetID }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Capsule()
                .fill(Color.clmixTrackBackground)
                .frame(width: 40, height: 4)
                .frame(maxWidth: .infinity)
                .padding(.top, 8)

            if let preset {
                Text(preset.name)
                    .font(.system(size: 16, weight: .bold))
                    .frame(maxWidth: .infinity)

                Toggle("Public", isOn: Binding(
                    get: { preset.isPublic },
                    set: { model.setPresetPublic(preset, isPublic: $0) }
                ))
                Text("Everyone with preset access can load it.")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)

                Text("Share with")
                    .font(.system(size: 15, weight: .semibold))
                    .padding(.top, 8)

                shareList(preset)

                Spacer(minLength: 0)

                Button(role: .destructive) {
                    confirmingDelete = true
                } label: {
                    Text("Delete Preset")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .foregroundStyle(Color.clmixMuteActive)
                .background(Color.clmixMuteInactive)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .confirmationDialog(
                    "Delete \"\(preset.name)\"? Anyone it is shared with loses it too.",
                    isPresented: $confirmingDelete, titleVisibility: .visible
                ) {
                    Button("Delete", role: .destructive) {
                        model.deletePreset(preset)
                        dismiss()
                    }
                }
            } else {
                Spacer()
            }
        }
        .padding(.horizontal, 24)
        .padding(.bottom, 12)
        .onAppear {
            if let preset { model.requestShareTargets(for: preset) }
        }
        // Gone from the list - deleted here or from the desktop.
        .onChange(of: preset == nil) { _, gone in
            if gone { dismiss() }
        }
    }

    @ViewBuilder
    private func shareList(_ preset: Preset) -> some View {
        if let targets = model.shareTargets {
            if targets.isEmpty {
                Text("No other accounts have preset access.")
                    .font(.system(size: 13))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
            } else {
                ScrollView {
                    VStack(spacing: 0) {
                        ForEach(targets, id: \.self) { user in
                            Toggle(model.shareTargetNames[user] ?? user, isOn: Binding(
                                get: { preset.sharedWith.contains(user) },
                                set: { on in
                                    var users = Set(preset.sharedWith)
                                    if on { users.insert(user) } else { users.remove(user) }
                                    model.sharePreset(preset, users: users.sorted())
                                }
                            ))
                            .toggleStyle(CheckboxToggleStyle())
                            .frame(minHeight: 44)
                        }
                    }
                }
            }
        } else {
            Text("Loading accounts\u{2026}")
                .font(.system(size: 13))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
        }
    }
}

/// A tick box, which iOS has no Toggle style for of its own.
private struct CheckboxToggleStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button {
            configuration.isOn.toggle()
        } label: {
            HStack(spacing: 12) {
                Image(systemName: configuration.isOn ? "checkmark.square.fill" : "square")
                    .font(.system(size: 20))
                    .foregroundStyle(configuration.isOn ? Color.clmixPrimary : Color.clmixOnSurfaceVariant)
                configuration.label
                    .foregroundStyle(Color.primary)
                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
