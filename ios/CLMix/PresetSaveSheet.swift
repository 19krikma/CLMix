import SwiftUI

/// Captures every channel's current level+pan for the active aux under a
/// name - the server reads the actual values from its own cache, so this
/// only needs to send the name. Mirrors Android's PresetSaveBottomSheet.
struct PresetSaveSheet: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var isSaving = false

    private var trimmedName: String {
        name.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(spacing: 16) {
            Capsule()
                .fill(Color.secondary.opacity(0.4))
                .frame(width: 40, height: 4)
                .padding(.top, 8)

            Text("Save Preset")
                .font(.headline)

            TextField("Preset Name", text: $name)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal, 24)

            Button(isSaving ? "Saving..." : "Save") {
                isSaving = true
                model.savePreset(name: trimmedName) {
                    isSaving = false
                    dismiss()
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(trimmedName.isEmpty || isSaving)
            .padding(.horizontal, 24)

            Spacer()
        }
    }
}
