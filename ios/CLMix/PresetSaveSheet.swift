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
                .fill(Color.clmixTrackBackground)
                .frame(width: 40, height: 4)
                .padding(.top, 8)

            Text("Save Preset")
                .font(.system(size: 16, weight: .bold))

            TextField("Preset Name", text: $name)
                .padding(.horizontal, 14)
                .frame(height: 52)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.clmixOutline, lineWidth: 1))
                .padding(.horizontal, 24)

            Button {
                isSaving = true
                model.savePreset(name: trimmedName) {
                    isSaving = false
                    dismiss()
                }
            } label: {
                Text(isSaving ? "Saving..." : "Save")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            }
            .foregroundStyle(Color.clmixOnPrimary)
            .background(Color.clmixPrimary)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .opacity(trimmedName.isEmpty || isSaving ? 0.5 : 1)
            .disabled(trimmedName.isEmpty || isSaving)
            .padding(.horizontal, 24)

            Spacer()
        }
    }
}
