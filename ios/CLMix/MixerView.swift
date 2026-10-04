import SwiftUI

/// Mirrors Android's MixerActivity: a custom top control row (menu
/// button, Fine toggle, bank pull-down, inline error label) directly over
/// the channel grid rather than a system nav bar - Android's theme has no
/// action bar here at all, and enterFullScreen() hides the system bars
/// too, so every pixel goes to the fader grid.
///
/// The aux picker is the bottom sheet, within thumb reach (AuxSheetView).
/// The menu button opens what is left of Android's side drawer - Presets
/// (expands in place to Save/Load), Dark mode, Log Out - as one sheet,
/// since iOS has no equivalent slide-out drawer.
struct MixerView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var themeStore: ThemeStore
    @Environment(\.colorScheme) var systemColorScheme
    let aux: AuxBus

    @State private var banksExpanded = false
    @State private var showMenu = false
    @State private var presetsExpanded = false
    @State private var showPresetSave = false
    @State private var showPresetLoad = false
    @State private var showCustomBanks = false

    /// Which side panel the landscape sidebar has out, if any.
    @State private var openPanel: LandscapePanel?

    private enum LandscapePanel { case menu, aux, bank }

    /// Puts the menu away wherever it currently is - the sheet in
    /// portrait, the sidebar's panel in landscape.
    private func dismissMenu() {
        showMenu = false
        withAnimation(panelAnimation) { openPanel = nil }
    }

    /// The landscape control strip, and the panel that comes out from
    /// under it. The strip is narrow on purpose: it lives on the edge
    /// the selfie camera is on, which is dead screen with the phone on
    /// its side, so it costs the faders nothing.
    private static let sidebarWidth: CGFloat = 58
    private static let panelWidth: CGFloat = 300

    private let panelAnimation = Animation.easeOut(duration: 0.18)

    // Deliberately well under Android's own 200ms "short" duration: this
    // is a control being operated mid-show, not a screen transition, so
    // it only has to take the hard edge off the strips jumping - any
    // longer reads as waiting for the panel. Decelerating suits a panel
    // being pulled down and released: it arrives rather than stopping
    // dead.
    private let bankPanelAnimation = Animation.easeOut(duration: 0.1)

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
        GeometryReader { geo in
            if geo.size.width > geo.size.height {
                landscapeBody(geo)
            } else {
                portraitBody
            }
        }
        // The fill ignores the safe area while the content above it does
        // not: rotated, the inset the notch takes out of the leading edge
        // would otherwise leave a bare strip down the side of the screen
        // in whatever colour the window happens to be under this. Nothing
        // moves - only the paint reaches further.
        .background(Color.clmixBackground.ignoresSafeArea())
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
        // Full height, unlike the preset sheets: this one is a screen's
        // worth of list - every channel on the desk, with a switch
        // against each - rather than a short panel.
        .sheet(isPresented: $showCustomBanks) {
            CustomBanksView()
        }
        // Presets are per-aux-send, so a sheet left open from the
        // previous aux would otherwise keep acting against the new one -
        // mirrors Android's switchAux dismissing them. The pan sheet
        // needs no equivalent: it belongs to a channel strip, and
        // switching aux clears the strips out from under it.
        .onChange(of: aux.index) { _, _ in
            showPresetSave = false
            showPresetLoad = false
        }
    }

    private var portraitBody: some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 0) {
                topBar

                if banksExpanded {
                    BankPanelView(
                        banks: model.banks,
                        selectedBank: model.selectedBank,
                        onSelect: { bank in
                            // Fold away on pick: the panel exists to make
                            // the choice, and leaving it open would keep a
                            // third of the faders pushed off screen after
                            // the choice is made.
                            withAnimation(bankPanelAnimation) { banksExpanded = false }
                            model.selectBank(bank)
                        }
                    )
                    .transition(.opacity)
                }

                channelGrid
            }
            // Reserves the collapsed sheet's strip of screen. Done here
            // rather than inside the grid: the sheet floats over the
            // strips and takes no layout height of its own, so without
            // this they would measure themselves all the way to the
            // bottom edge and put their Mute buttons underneath it.
            .padding(.bottom, AuxSheetView.peekHeight)

            AuxSheetView(
                auxes: model.auxes,
                currentIndex: aux.index,
                onSelect: { model.switchAux($0) }
            )
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// The sideways layout: no top bar and no aux sheet, because
    /// everything they held now lives in the strip down the camera's
    /// edge - and the strips get back the height those two were using,
    /// which is what they were short of. Mirrors Android's
    /// MixerActivity.applyOrientation.
    private func landscapeBody(_ geo: GeometryProxy) -> some View {
        // Whichever edge the notch eats into is the edge the camera is
        // on. Read off the safe area rather than off the interface
        // orientation: this is the measurement that actually says where
        // the hardware is, and it is already to hand.
        let cameraLeading = geo.safeAreaInsets.leading >= geo.safeAreaInsets.trailing
        let edge: Edge.Set = cameraLeading ? .leading : .trailing

        return ZStack(alignment: cameraLeading ? .leading : .trailing) {
            // Stops at the sidebar rather than running under it: a fader
            // half behind a button is one that cannot be grabbed by its
            // bottom half.
            channelGrid
                .padding(edge, Self.sidebarWidth)

            if openPanel != nil {
                // Catches the tap that closes the panel, over the strips
                // so dismissing it cannot also move a fader.
                Color.black.opacity(0.6)
                    .ignoresSafeArea()
                    .onTapGesture { withAnimation(panelAnimation) { openPanel = nil } }
            }

            if let panel = openPanel {
                // Inset by the sidebar so it comes out beside it rather
                // than under it - the sidebar is what closes the panel
                // again, so it cannot be the thing the panel covers.
                landscapePanel(panel)
                    .padding(edge, Self.sidebarWidth)
                    .transition(.move(edge: cameraLeading ? .leading : .trailing))
            }

            landscapeSidebar(cameraLeading: cameraLeading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // With both bars gone the status bar's strip is the last thing
        // between a fader and the top of the screen. The bottom is left
        // alone: the Mute buttons are down there and the home indicator
        // would sit on them.
        .ignoresSafeArea(edges: .top)
    }

    private func landscapeSidebar(cameraLeading: Bool) -> some View {
        VStack(spacing: 0) {
            Button {
                withAnimation(panelAnimation) {
                    openPanel = openPanel == .menu ? nil : .menu
                }
            } label: {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 20, weight: .medium))
                    .frame(width: 44, height: 44)
            }
            .foregroundStyle(Color.primary)
            .padding(.top, 10)

            sidebarPanelButton("AUX", panel: .aux, cameraLeading: cameraLeading)
                .padding(.top, 8)

            // The whole middle of the strip, left empty on purpose:
            // this is the stretch the camera and its surround sit in,
            // so a control there would be a control under the glass.
            // AUX rides up against the menu and BANK down against
            // Fine, which also puts the two of them as far apart as
            // the strip allows - a thumb reaching for one mid-show
            // cannot catch the other.
            Spacer()

            sidebarPanelButton("BANK", panel: .bank, cameraLeading: cameraLeading)

            Button {
                model.fineMode.toggle()
            } label: {
                Text("Fine")
                    .font(.system(size: 11))
                    .frame(width: 48, height: 38)
            }
            .background(model.fineMode ? Color.clmixSecondary : Color.clmixMuteInactive)
            .foregroundStyle(
                model.fineMode ? Color.clmixOnSecondary : Color.clmixOnMuteInactive
            )
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .padding(.top, 8)
            .padding(.bottom, 12)
        }
        .frame(width: Self.sidebarWidth)
        .frame(maxHeight: .infinity)
        .background(Color.clmixSurface.ignoresSafeArea())
    }

    /// Chevron over label rather than beside it: 58pt has no room for
    /// the two side by side, and the arrow is the part that says what
    /// will happen. It points away from its own edge - the way the
    /// panel will travel - so it flips with the phone.
    private func sidebarPanelButton(
        _ title: String, panel: LandscapePanel, cameraLeading: Bool
    ) -> some View {
        Button {
            withAnimation(panelAnimation) {
                openPanel = openPanel == panel ? nil : panel
            }
        } label: {
            VStack(spacing: 2) {
                Image(systemName: cameraLeading ? "chevron.right" : "chevron.left")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(Color.clmixPrimary)

                Text(title)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Color.primary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
        }
    }

    @ViewBuilder
    private func landscapePanel(_ panel: LandscapePanel) -> some View {
        if panel == .menu {
            // The drawer's own content, slid out of the sidebar rather
            // than presented over everything: it comes from the right
            // edge and stops short of the strip, so the buttons that
            // opened it stay on screen and can close it again.
            VStack(alignment: .leading, spacing: 0) {
                Text("MENU")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
                    .padding(.horizontal, 16)
                    .padding(.top, 14)

                menuContent
            }
            .frame(width: Self.panelWidth)
            .frame(maxHeight: .infinity)
            .background(Color.clmixSurface.ignoresSafeArea())
        } else {
            auxOrBankPanel(panel)
        }
    }

    private func auxOrBankPanel(_ panel: LandscapePanel) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(panel == .aux ? "AUX" : "BANK")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
                .padding(.horizontal, 16)
                .padding(.top, 14)

            ScrollView {
                VStack(spacing: 8) {
                    if panel == .aux {
                        ForEach(model.auxes) { bus in
                            panelRow(bus.name, selected: bus.index == aux.index) {
                                withAnimation(panelAnimation) { openPanel = nil }
                                model.switchAux(bus)
                            }
                        }
                    } else {
                        ForEach(model.banks, id: \.self) { bank in
                            panelRow(bank, selected: bank == model.selectedBank) {
                                withAnimation(panelAnimation) { openPanel = nil }
                                model.selectBank(bank)
                            }
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 12)
            }
        }
        .frame(width: Self.panelWidth)
        .frame(maxHeight: .infinity)
        .background(Color.clmixSurface.ignoresSafeArea())
    }

    private func panelRow(
        _ title: String, selected: Bool, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14)
                .frame(height: 46)
        }
        .foregroundStyle(selected ? Color.clmixOnPrimary : Color.primary)
        .background(selected ? Color.clmixPrimary : Color.clmixSurfaceVariant)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private var channelGrid: some View {
        // A horizontal ScrollView only sizes itself to its content's
        // height by default, not the space available in the VStack's own
        // (vertical) stacking axis - without forcing it to fill, the
        // fader grid hugs its own height and everything below that height
        // renders as bare background instead of scrolling real estate.
        // alignment: .top on that forced frame matters just as much as
        // the frame itself: frame(maxHeight:)'s default alignment is
        // .center, which would otherwise center the (still content-sized)
        // HStack within the new taller frame instead of pinning it to the
        // top.
        //
        // Everything a strip draws is read out of the model here and
        // handed down as a value, and .equatable() lets SwiftUI skip the
        // ones that did not change - see ChannelStripView for why a
        // strip deliberately does not reach into the environment for
        // this itself.
        ScrollView(.horizontal) {
            HStack(alignment: .top, spacing: 0) {
                ForEach(Array(model.channels.enumerated()), id: \.element.id) { index, channel in
                    ChannelStripView(
                        channel: channel,
                        fineMode: model.fineMode,
                        alternate: index % 2 == 1,
                        panSupported: model.panSupported,
                        muteOffered: model.muteOffered,
                        personalizationAllowed: model.personalizationAllowed,
                        liveSnapshot: model.liveSnapshot,
                        onLevel: { model.setLevel(channel: channel.channel, db: $0) },
                        onPan: { model.setPan(channel: channel.channel, pan: $0) },
                        onMute: { model.setMute(channel: channel.channel, muted: $0) },
                        onPersonalName: {
                            model.setPersonalName(channel: channel.channel, name: $0)
                        }
                    )
                    .equatable()
                }
            }
            .padding(10)
            .frame(maxHeight: .infinity, alignment: .top)
        }
        .frame(maxHeight: .infinity)
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

            // REMOVE WITH DEMO MODE.
            if model.isDemo {
                Text("DEMO")
                    .font(.system(size: 10, weight: .heavy))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(Color.clmixSecondary)
                    .foregroundStyle(Color.clmixOnSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: 4))
            }

            // Takes the room the bank dropdown used to, so Fine and the
            // bank toggle stay pinned to the right edge.
            Spacer(minLength: 0)

            fineButton
            bankToggle

            // Surfaces a mid-session protocol rejection (e.g. "not
            // permitted for this aux") in place - mirrors Android's
            // status_label. Gated on statusIsError, not just non-empty:
            // statusMessage still holds "Connected" from the login flow
            // at this point (AppModel never clears it on screen
            // transitions), which isn't an error and was never meant to
            // show here.
            if model.statusIsError && !model.statusMessage.isEmpty {
                Text(model.statusMessage)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixMuteActive)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(Color.clmixSurface)
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

    // Pulls the bank panel down. Points the way it will move, not at what
    // it is - so it flips to point up while the panel is open.
    private var bankToggle: some View {
        Button {
            withAnimation(bankPanelAnimation) { banksExpanded.toggle() }
        } label: {
            Image(systemName: "chevron.down")
                .font(.system(size: 16, weight: .semibold))
                .rotationEffect(.degrees(banksExpanded ? 180 : 0))
                .frame(width: 40, height: 40)
        }
        .disabled(model.banks.isEmpty)
        .opacity(model.banks.isEmpty ? 0.35 : 1)
        .accessibilityLabel(banksExpanded ? "Hide banks" : "Show banks")
    }

    // What is left of Android's drawer once the aux list moved out of it
    // and became the bottom sheet: the settings-ish actions that were
    // always underneath it.
    private var menuSheet: some View {
        menuContent
            .frame(maxWidth: .infinity)
            .background(Color.clmixSurface)
            .presentationDetents([.medium])
    }

    /// The menu itself, with no opinion about what is presenting it:
    /// portrait puts it in a sheet, landscape slides it out of the
    /// sidebar like the other two panels. One copy rather than two, so
    /// a button added here cannot go missing from one of them.
    private var menuContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Held down at the bottom, in thumb reach - in the landscape
            // panel just as much as in the sheet, since the hand holding
            // the phone is at the bottom either way.
            Spacer()

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
                            dismissMenu()
                            showPresetSave = true
                        }
                        tonalButton("Load") {
                            dismissMenu()
                            showPresetLoad = true
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.top, 8)
                }
            }

            // Behind the personalization permission, which is the same
            // one personal channel names sit behind: both are this
            // account's own view of a shared console. Aux screens only -
            // Mixer Control shows the desk's own grouping, the way it
            // shows the desk's own names.
            if model.personalizationAllowed {
                tonalButton("Custom Banks") {
                    dismissMenu()
                    showCustomBanks = true
                }
                .padding(.horizontal, 14)
                .padding(.top, 10)
            }

            Toggle("Dark mode", isOn: darkModeBinding)
                .padding(.horizontal, 14)
                .padding(.top, 16)

            // Back returns to the AUX Only / Mixer Control choice; Log
            // Out ends the session outright. Side by side because they
            // are the same kind of action at different depths - the
            // same pair, in the same order, as the mixer control
            // screen's own sheet.
            //
            // Back only exists for an account that was offered that
            // choice: everyone else came straight here from login and
            // has no second mode to cross to, so for them there is
            // nothing behind this screen but the login form, and Log
            // Out keeps the row to itself.
            HStack(spacing: 12) {
                if model.mixerControlAllowed {
                    Button {
                        dismissMenu()
                        model.leaveAux()
                    } label: {
                        Text("Back")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .foregroundStyle(Color.clmixOnMuteInactive)
                    .background(Color.clmixMuteInactive)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }

                Button {
                    dismissMenu()
                    model.logout()
                } label: {
                    Text("Log Out")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .foregroundStyle(Color.clmixOnPrimary)
                .background(Color.clmixPrimary)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .padding(14)
        }
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
