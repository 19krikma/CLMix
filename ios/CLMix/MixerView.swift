import SwiftUI

/// Mirrors Android's MixerActivity: a custom top control row (menu
/// button, bank picker, Fine toggle, inline error label) directly over
/// the channel grid rather than a system nav bar - Android's theme has no
/// action bar here at all, and enterFullScreen() hides the system bars
/// too, so every pixel goes to the fader grid. The menu button opens
/// everything Android's side drawer holds - Aux Buses, Presets (expands
/// in place to Save/Load), Dark mode, Log Out - as one sheet, since iOS
/// has no equivalent slide-out drawer.
struct MixerView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var themeStore: ThemeStore
    @Environment(\.colorScheme) var systemColorScheme
    let aux: AuxBus

    @State private var selectedBank: String?
    @State private var showMenu = false
    @State private var presetsExpanded = false
    @State private var showPresetSave = false
    @State private var showPresetLoad = false

    // What the toggle should show. With no saved choice there's nothing
    // stored to read, so this falls back to what's actually on screen
    // right now - mirrors Android's ThemeStore.isDarkMode, so the switch
    // starts out agreeing with the system rather than always starting off.
    private var darkModeBinding: Binding<Bool> {
        Binding(
            get: { themeStore.isDarkMode ?? (systemColorScheme == .dark) },
            set: { themeStore.isDarkMode = $0 }
        )
    }

    var body: some View {
        VStack(spacing: 0) {
            topBar

            // A horizontal ScrollView only sizes itself to its content's
            // height by default, not the space available in the VStack's
            // own (vertical) stacking axis - without forcing it to fill,
            // the fader grid hugs its own height and everything below
            // that height renders as bare background instead of scrolling
            // real estate. alignment: .top on that forced frame matters
            // just as much as the frame itself: frame(maxHeight:)'s
            // default alignment is .center, which would otherwise center
            // the (still content-sized) HStack within the new taller
            // frame instead of pinning it to the top - Android's
            // RecyclerView lays channels out from the top with no such
            // centering, and the .top on the HStack itself only aligns
            // children within its own bounds, not the enclosing frame.
            ScrollView(.horizontal) {
                HStack(alignment: .top, spacing: 0) {
                    ForEach(Array(model.channels.enumerated()), id: \.element.id) { index, channel in
                        ChannelStripView(
                            channel: channel, fineMode: model.fineMode, alternate: index % 2 == 1
                        )
                    }
                }
                .padding(10)
                .frame(maxHeight: .infinity, alignment: .top)
            }
            .frame(maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clmixBackground)
        .navigationBarHidden(true)
        .sheet(isPresented: $showMenu) { menuSheet }
        .sheet(isPresented: $showPresetSave) {
            PresetSaveSheet()
                .presentationDetents([.height(260)])
        }
        .sheet(isPresented: $showPresetLoad) {
            PresetLoadSheet()
                .presentationDetents([.medium, .large])
        }
    }

    private var topBar: some View {
        HStack(spacing: 10) {
            Button {
                showMenu = true
            } label: {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 18))
                    .frame(width: 40, height: 40)
            }

            Text("Bank")
                .font(.system(size: 14))
                .foregroundStyle(Color.clmixOnSurfaceVariant)

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
            .frame(maxWidth: .infinity, alignment: .leading)

            fineButton

            // Surfaces a mid-session protocol rejection (e.g. "not
            // permitted for this aux") in place - mirrors Android's
            // status_label. Gated on statusIsError, not just non-empty:
            // statusMessage still holds "Connected" from the login flow
            // at this point (AppModel never clears it on screen
            // transitions), which isn't an error and was never meant to
            // show here - Android's status_label only ever carries error
            // text on this screen to begin with.
            if model.statusIsError && !model.statusMessage.isEmpty {
                Text(model.statusMessage)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixMuteActive)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(Color(uiColor: .secondarySystemBackground))
    }

    private var fineButton: some View {
        Button("Fine") {
            model.fineMode.toggle()
        }
        .font(.system(size: 12, weight: .semibold))
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(model.fineMode ? Color.clmixSecondary : Color.clmixMuteInactive)
        .foregroundStyle(model.fineMode ? Color.clmixOnSecondary : Color.clmixOnMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private var menuSheet: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Aux Buses")
                .font(.system(size: 16, weight: .bold))
                .padding(.horizontal, 18)
                .padding(.top, 20)
                .padding(.bottom, 8)

            List(model.auxes) { otherAux in
                Button(otherAux.name) {
                    showMenu = false
                    if otherAux.index != aux.index {
                        model.selectAux(otherAux)
                    }
                }
                .foregroundStyle(.primary)
            }
            .listStyle(.plain)

            Divider()

            // Only accounts with Preset Access (Setup > Accounts on
            // desktop) get this at all - the server enforces the same
            // check independently, but there's no point showing an
            // action that would just come back as an error.
            if model.presetsAllowed {
                tonalButton("Presets") {
                    withAnimation { presetsExpanded.toggle() }
                }
                .padding(.horizontal, 14)
                .padding(.top, 10)

                if presetsExpanded {
                    HStack(spacing: 12) {
                        tonalButton("Save") {
                            showMenu = false
                            showPresetSave = true
                        }
                        tonalButton("Load") {
                            showMenu = false
                            showPresetLoad = true
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 8)
                }
            }

            Toggle("Dark mode", isOn: darkModeBinding)
                .padding(.horizontal, 14)
                .padding(.top, 16)

            Button {
                showMenu = false
                model.logout()
            } label: {
                Text("Log Out")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            }
            .foregroundStyle(Color.clmixOnPrimary)
            .background(Color.clmixPrimary)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .padding(14)
        }
        .presentationDetents([.medium, .large])
    }

    private func tonalButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
        }
        .foregroundStyle(Color.clmixOnMuteInactive)
        .background(Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}
