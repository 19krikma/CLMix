import SwiftUI

/// This account's own banks: which ones exist, and which of the desk's
/// channels are under each.
///
/// Reached from the aux mixer's menu, and only by an account holding
/// personalization - the same permission as personal channel names,
/// because this is the same kind of thing: one account's view of a
/// console everyone else is sharing. Nothing here reaches the desk, and
/// no other phone sees any of it.
///
/// The server owns the set. This view never edits in place and hopes:
/// every change sends the whole set and redraws from the reply, so what
/// is on screen is always what was actually stored - including the
/// server's own cleaning up of it (see UserStore._clean_banks). Mirrors
/// Android's CustomBanksActivity.
struct CustomBanksView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    /// Which bank's channels the lower list is showing. By name rather
    /// than index, so a set that comes back reordered or shorter does
    /// not silently select a different bank than the one that was
    /// selected.
    @State private var selectedName: String?

    /// The bank being edited, by its name as the server knows it, and
    /// the edits so far. Held apart from the stored set so cancelling
    /// is free.
    @State private var editingName: String?
    @State private var draftName = ""
    @State private var draftChannels: Set<Int> = []

    @State private var showAddPrompt = false
    @State private var newBankName = ""
    @State private var confirmRemove = false
    @State private var confirmReset = false
    @State private var nameClash: String?

    private var selected: CustomBank? {
        model.customBanks.first { $0.name == selectedName }
    }

    var body: some View {
        NavigationStack {
            Group {
                if editingName != nil {
                    editor
                } else {
                    browser
                }
            }
            .background(Color.clmixBackground.ignoresSafeArea())
            .navigationTitle(editingName == nil ? "Custom Banks" : "Edit Bank")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    if editingName != nil {
                        Button("Cancel") { editingName = nil }
                    } else {
                        Button("Done") { dismiss() }
                    }
                }

                ToolbarItem(placement: .confirmationAction) {
                    if editingName != nil {
                        Button("Save") { saveEdit() }
                    }
                }
            }
        }
        .task { model.requestCustomBanks() }
        .onChange(of: model.customBanks) { _, banks in
            if !banks.contains(where: { $0.name == selectedName }) {
                selectedName = banks.first?.name
            }
        }
        .alert("New bank", isPresented: $showAddPrompt) {
            TextField("Bank name", text: $newBankName)
            Button("Cancel", role: .cancel) { newBankName = "" }
            Button("Add") { addBank() }
        }
        .alert(
            "Remove \"\(selected?.name ?? "")\"?",
            isPresented: $confirmRemove
        ) {
            Button("Cancel", role: .cancel) {}
            Button("Remove", role: .destructive) { removeSelected() }
        } message: {
            Text("The channels stay on the desk - only your grouping goes.")
        }
        .alert("Reset to the mixer's banks?", isPresented: $confirmReset) {
            Button("Cancel", role: .cancel) {}
            Button("Reset", role: .destructive) {
                selectedName = nil
                model.resetCustomBanks()
            }
        } message: {
            Text("Your banks are replaced by the ones the console reports.")
        }
        .alert(
            "That name is taken",
            isPresented: Binding(
                get: { nameClash != nil },
                set: { if !$0 { nameClash = nil } }
            )
        ) {
            Button("OK", role: .cancel) { nameClash = nil }
        } message: {
            Text("There is already a bank called \"\(nameClash ?? "")\".")
        }
    }

    // MARK: - Browsing

    private var browser: some View {
        VStack(spacing: 0) {
            Text("Your own grouping of the desk's channels. Only you see it.")
                .font(.system(size: 13))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.bottom, 12)

            // The two lists share one scroll: on a phone the bank list
            // plus a long channel list does not fit, and scrolling them
            // separately would leave the user dragging the wrong one.
            ScrollView {
                VStack(spacing: 8) {
                    ForEach(model.customBanks) { bank in
                        bankRow(bank)
                    }

                    if model.customBanks.isEmpty {
                        Text("No banks. Add one, or reset to the mixer's.")
                            .font(.system(size: 14))
                            .foregroundStyle(Color.clmixOnSurfaceVariant)
                            .padding(.vertical, 20)
                    }

                    if let selected {
                        Text(
                            "\(selected.channels.count) channels in \"\(selected.name)\""
                        )
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(Color.clmixOnSurfaceVariant)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 16)

                        if selected.channels.isEmpty {
                            Text("Nothing in this bank yet - tap Edit to pick channels.")
                                .font(.system(size: 14))
                                .foregroundStyle(Color.clmixOnSurfaceVariant)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        } else {
                            // In the console's order rather than the
                            // order they were ticked, which is how every
                            // other channel list in the app reads.
                            ForEach(selected.channels.sorted(), id: \.self) { channel in
                                Text("\(channel)   \(nameFor(channel))")
                                    .font(.system(size: 14))
                                    .foregroundStyle(Color.primary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.vertical, 4)
                            }
                        }
                    }
                }
                .padding(.horizontal, 14)
                .padding(.bottom, 12)
            }

            // Add/Edit/Remove act on the selected bank, so they sit
            // under the list they act on.
            HStack(spacing: 8) {
                actionButton("Add", filled: true) {
                    newBankName = ""
                    showAddPrompt = true
                }
                actionButton("Edit", filled: false) { startEditing() }
                    .disabled(selected == nil)
                actionButton("Remove", filled: false) { confirmRemove = true }
                    .disabled(selected == nil)
            }
            .padding(.horizontal, 14)

            // Reset is the way back to the desk's own grouping, and the
            // one destructive action here that needs no bank selected -
            // so it is kept apart from the three above it.
            Button("Reset to the mixer's banks") { confirmReset = true }
                .font(.system(size: 15))
                .foregroundStyle(Color.clmixPrimary)
                .padding(.top, 10)
                .padding(.bottom, 14)
        }
    }

    private func bankRow(_ bank: CustomBank) -> some View {
        let isSelected = bank.name == selectedName

        return Button {
            selectedName = bank.name
        } label: {
            Text("\(bank.name)  (\(bank.channels.count))")
                .frame(maxWidth: .infinity)
                .frame(height: 48)
        }
        // The same selected/unselected pair the discovered server rows
        // use on the connect screen, so "the one I picked" looks the
        // same everywhere in the app.
        .foregroundStyle(isSelected ? Color.clmixOnPrimary : Color.primary)
        .background(isSelected ? Color.clmixPrimary : Color.clear)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(isSelected ? Color.clear : Color.clmixOutline, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func actionButton(
        _ title: String, filled: Bool, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .frame(maxWidth: .infinity)
                .frame(height: 46)
        }
        .foregroundStyle(filled ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
        .background(filled ? Color.clmixPrimary : Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func nameFor(_ channel: Int) -> String {
        model.bankChannels.first { $0.channel == channel }?.name ?? "Ch \(channel)"
    }

    // MARK: - Editing one bank

    private var editor: some View {
        VStack(spacing: 0) {
            // The name is an ordinary field rather than a separate
            // Rename action: renaming and re-picking the channels are
            // the same edit as far as the user is concerned.
            TextField("Bank name", text: $draftName)
                .padding(.horizontal, 14)
                .frame(height: 52)
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.clmixOutline, lineWidth: 1)
                )
                .padding(.horizontal, 14)

            Text("\(draftChannels.count) of \(model.bankChannels.count) channels")
                .font(.system(size: 13))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 20)
                .padding(.top, 10)
                .padding(.bottom, 4)

            ScrollView {
                VStack(spacing: 0) {
                    ForEach(model.bankChannels) { channel in
                        Toggle(isOn: Binding(
                            get: { draftChannels.contains(channel.channel) },
                            set: { on in
                                if on {
                                    draftChannels.insert(channel.channel)
                                } else {
                                    draftChannels.remove(channel.channel)
                                }
                            }
                        )) {
                            Text("\(channel.channel)   \(channel.name)")
                                .font(.system(size: 15))
                        }
                        .toggleStyle(.switch)
                        .padding(.vertical, 6)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 12)
            }
        }
    }

    // MARK: - Actions

    private func addBank() {
        let name = newBankName.trimmingCharacters(in: .whitespaces)
        newBankName = ""

        guard !name.isEmpty else { return }

        if model.customBanks.contains(where: { $0.name.lowercased() == name.lowercased() }) {
            nameClash = name
            return
        }

        // Created empty and selected, so the obvious next tap is Edit -
        // rather than throwing the user into a channel list they did
        // not ask for yet.
        selectedName = name
        model.saveCustomBanks(model.customBanks + [CustomBank(name: name, channels: [])])
    }

    private func removeSelected() {
        guard let selected else { return }
        selectedName = nil
        model.saveCustomBanks(model.customBanks.filter { $0.name != selected.name })
    }

    private func startEditing() {
        guard let selected else { return }
        editingName = selected.name
        draftName = selected.name
        draftChannels = Set(selected.channels)
    }

    private func saveEdit() {
        guard let original = editingName else { return }
        let name = draftName.trimmingCharacters(in: .whitespaces)

        guard !name.isEmpty else { return }

        let clash = model.customBanks.contains {
            $0.name != original && $0.name.lowercased() == name.lowercased()
        }

        if clash {
            nameClash = name
            return
        }

        // Replaced in place rather than removed and appended, so a
        // rename does not move the bank to the bottom of the picker.
        let updated = model.customBanks.map { bank in
            bank.name == original
                ? CustomBank(name: name, channels: draftChannels.sorted())
                : bank
        }

        selectedName = name
        editingName = nil
        model.saveCustomBanks(updated)
    }
}
