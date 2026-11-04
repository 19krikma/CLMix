import SwiftUI

/// Mirrors Android's MixerActivity: a custom top control row (menu
/// button, Fine toggle, bank pull-down, inline error label) directly over
/// the channel grid rather than a system nav bar - Android's theme has no
/// action bar here at all, and enterFullScreen() hides the system bars
/// too, so every pixel goes to the fader grid.
///
/// The aux picker is the bottom sheet, within thumb reach (AuxSheetView).
/// The menu button opens what is left of Android's side drawer - Presets
/// (expands in place to Save/Load), Log Out - as one sheet,
/// since iOS has no equivalent slide-out drawer.
struct MixerView: View {
    @EnvironmentObject var model: AppModel
    let aux: AuxBus

    @State private var banksExpanded = false

    /// Whether the bank panel is out: always on a tablet, where there is
    /// room to leave it, else whatever the toggle last said. Picking a
    /// bank folds it on a phone and does nothing to it here.
    private var showBanks: Bool { banksExpanded || LargeScreen.isActive }
    @State private var showMenu = false
    @State private var presetsExpanded = false
    @State private var showPresetSave = false
    @State private var showPresetLoad = false
    @State private var showChat = false
    @State private var showProfile = false

    // Bank edit mode (the pencil under the bank grid). Only ever on for
    // an account holding personalization. While on, the grid ends in a
    // "+" bank, the strips are led by a "+" channel, and banks and strips
    // are reordered by holding and dragging. Mirrors Android's
    // MixerActivity.setBankEditing.
    @State private var bankEditing = false
    @State private var newBankPlaceholder = false
    @State private var askingBankName = false
    @State private var newBankName = ""
    @State private var newBankError: String?
    @State private var renamingBank = false
    @State private var renameText = ""
    @State private var renameError: String?
    @State private var showBankChannels = false
    @State private var draggingChannel: Int?
    @State private var channelDropTarget: Int?
    @State private var draggingPanelBank: Int?
    @State private var panelBankDropTarget: Int?

    /// A strip's width (ChannelStripView.width) - the step between the
    /// gaps a dragged strip can land in.
    private static let stripPitch: CGFloat = 76
    /// A landscape bank row and the spacing under it.
    private static let panelRowPitch: CGFloat = 54

    /// Which side panel the landscape sidebar has out, if any.
    @State private var openPanel: LandscapePanel?

    private enum LandscapePanel { case menu, aux, bank }

    /// Which side of the screen the camera is on while the phone is on
    /// its side. Held in state rather than worked out from the layout:
    /// turning the phone end for end leaves the size and the safe area
    /// exactly as they were (iOS reports the same inset on both sides in
    /// landscape), so nothing the layout can measure says it happened.
    @State private var cameraOnLeft = true

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

    var body: some View {
        GeometryReader { geo in
            // Phones only: a tablet on its side still has the height for
            // the portrait layout, bars, banks and all.
            if geo.size.width > geo.size.height && !LargeScreen.isActive {
                landscapeBody(geo)
            } else {
                portraitBody(geo)
            }
        }
        // The fill ignores the safe area while the content above it does
        // not: rotated, the inset the notch takes out of the leading edge
        // would otherwise leave a bare strip down the side of the screen
        // in whatever colour the window happens to be under this. Nothing
        // moves - only the paint reaches further.
        .background(Color.clmixBackground.ignoresSafeArea())
        .navigationBarHidden(true)
        .overlay { SosNotice() }
        .overlay { SosOptions() }
        .sheet(isPresented: $showMenu) { menuSheet }
        .sheet(isPresented: $showPresetSave) {
            PresetSaveSheet()
                .presentationDetents([.height(260)])
        }
        .sheet(isPresented: $showPresetLoad) {
            PresetLoadSheet()
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showProfile) {
            ProfileView()
        }
        .sheet(isPresented: $showChat) {
            ChatView()
        }
        // Slides up over the strips, which change behind it as channels
        // are ticked.
        .sheet(isPresented: $showBankChannels) {
            if let bank = model.selectedBank {
                BankChannelsSheet(bankName: bank)
                    .presentationDetents([.large, .medium])
            }
        }
        // A bank with no name is not allowed: leaving the name empty or
        // cancelling takes the "New bank" tile away again.
        .alert("New bank", isPresented: $askingBankName) {
            TextField("Bank name", text: $newBankName)
                .textInputAutocapitalization(.words)
            Button("Cancel", role: .cancel) { newBankPlaceholder = false }
            Button("Create") { finishNewBank() }
        } message: {
            if let newBankError { Text(newBankError) }
        }
        // Renames the selected bank. An empty name, or the same one,
        // leaves it as it was; another bank's name is refused and asked
        // again with the reason.
        .alert("Rename bank", isPresented: $renamingBank) {
            TextField("Bank name", text: $renameText)
                .textInputAutocapitalization(.words)
            Button("Cancel", role: .cancel) {}
            Button("Save") { finishRename() }
        } message: {
            if let renameError { Text(renameError) }
        }
        .onChange(of: banksExpanded) { _, expanded in
            // Edit mode lives in the panel; folding it away ends it.
            if !expanded { setBankEditing(false) }
        }
        .onChange(of: openPanel) { _, panel in
            if panel != .bank && !showBanks { setBankEditing(false) }
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
        .onAppear {
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
            readInterfaceOrientation()

            // Loaded ahead of time, so the first tap in edit mode already
            // has the set to work on.
            if model.personalizationAllowed { model.requestCustomBanks() }
        }
        .onDisappear {
            UIDevice.current.endGeneratingDeviceOrientationNotifications()
        }
        .onReceive(
            NotificationCenter.default.publisher(for: UIDevice.orientationDidChangeNotification)
        ) { _ in
            // The device's own reading, not the interface's: this arrives
            // before the interface has turned, so the interface would
            // still be answering for the old way up. Face up, face down
            // and portrait say nothing about which end the camera is, so
            // they leave the last landscape answer standing. Note the
            // device's names run opposite to the interface's: landscape
            // left is the device turned left, which puts its top - and
            // the camera - on the left.
            switch UIDevice.current.orientation {
            case .landscapeLeft: cameraOnLeft = true
            case .landscapeRight: cameraOnLeft = false
            default: break
            }
        }
    }

    /// The starting answer, for a screen that opens with the phone
    /// already sideways and so never sees it turn.
    private func readInterfaceOrientation() {
        let scene = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first
        switch scene?.effectiveGeometry.interfaceOrientation {
        case .landscapeRight: cameraOnLeft = true
        case .landscapeLeft: cameraOnLeft = false
        default: break
        }
    }

    private func portraitBody(_ geo: GeometryProxy) -> some View {
        let menuDocked = LargeScreen.docksMenu(geo.size)

        return HStack(spacing: 0) {
            // An iPad on its side keeps the menu out down the left, with
            // the strips and the aux sheet beside it rather than under it.
            if menuDocked {
                menuContent
                    .frame(width: LargeScreen.menuWidth)
                    .frame(maxHeight: .infinity)
                    .background(Color.clmixSurface)
            }

            mixerColumn(geo, menuDocked: menuDocked)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .ignoresSafeArea(edges: [.top, .bottom])
    }

    private func mixerColumn(_ geo: GeometryProxy, menuDocked: Bool) -> some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 0) {
                // A phone's bar is exactly the camera strip or its own
                // height, whichever is taller; a tablet's grows past that
                // to hold the banks in its middle.
                let barHeight = max(geo.safeAreaInsets.top, Self.topBarHeight)
                topBar(menuDocked: menuDocked)
                    .frame(height: LargeScreen.isActive ? nil : barHeight)
                    .frame(minHeight: LargeScreen.isActive ? barHeight : nil)
                    .background(Color.clmixSurface)

                if banksExpanded && !LargeScreen.isActive {
                    bankPanel(inTopBar: false)
                        .transition(.opacity)
                }

                channelGrid
            }
            // Reserves the collapsed sheet's strip of screen. Done here
            // rather than inside the grid: the sheet floats over the
            // strips and takes no layout height of its own, so without
            // this they would measure themselves all the way to the
            // bottom edge and put their Mute buttons underneath it.
            .padding(.bottom, AuxSheetView.peekHeight + SnapshotBar.height)

            // The aux sheet stands on the snapshot strip rather than on
            // the bottom edge, so its handle sits just above it.
            AuxSheetView(
                auxes: model.auxes,
                currentIndex: aux.index,
                users: model.auxUsers,
                me: model.profile?.username,
                onSelect: { model.switchAux($0) }
            )
            .padding(.bottom, SnapshotBar.height)

            SnapshotBar(snapshot: model.liveSnapshot)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // Full screen hides the status bar, but the safe area still keeps
        // the strip the camera sits in - and, at the bottom, the one the
        // home indicator sits in. The top bar moves up into the camera's
        // strip (its middle is a spacer, so the cutout lands on nothing),
        // and the aux sheet runs to the bottom edge: its name sits well
        // above where the indicator draws, and a tap on it never reaches
        // the system's swipe.
        .ignoresSafeArea(edges: [.top, .bottom])
    }

    /// The bank grid: under the top bar on a phone, folded away until the
    /// arrow is pressed; in the middle of the bar on a tablet.
    private func bankPanel(inTopBar: Bool) -> some View {
        BankPanelView(
            banks: model.banks,
            selectedBank: model.selectedBank,
            onSelect: { bank in
                // Fold away on pick: the panel exists to make the choice,
                // and leaving it open would keep a third of the faders
                // pushed off screen after the choice is made. Not while
                // editing, where picking is choosing which bank to work
                // on - and never on a tablet, where it is always out.
                if !bankEditing && !inTopBar {
                    withAnimation(bankPanelAnimation) { banksExpanded = false }
                }
                model.selectBank(bank)
            },
            canEdit: model.personalizationAllowed,
            editing: bankEditing,
            placeholder: newBankPlaceholder,
            onToggleEdit: { setBankEditing(!bankEditing) },
            onRename: selectedBankEditable ? startRename : nil,
            onAdd: startNewBank,
            onMove: { model.moveBank(from: $0, to: $1) },
            inTopBar: inTopBar
        )
    }

    /// The top bar's own height when there is no camera strip to fill:
    /// a 40pt button plus its padding.
    private static let topBarHeight: CGFloat = 52

    /// The sideways layout: no top bar and no aux sheet, because
    /// everything they held now lives in the strip down the camera's
    /// edge - and the strips get back the height those two were using,
    /// which is what they were short of. Mirrors Android's
    /// MixerActivity.applyOrientation.
    private func landscapeBody(_ geo: GeometryProxy) -> some View {
        let cameraLeading = cameraOnLeft
        let edge: Edge.Set = cameraLeading ? .leading : .trailing

        // The strip fills the camera's side of the safe area rather than
        // sitting inside it: that band is otherwise bare background, and
        // the camera only takes its middle, which the strip leaves empty
        // anyway. Never narrower than the strip's own width, for phones
        // with no cutout to fill.
        let cameraInset = cameraLeading ? geo.safeAreaInsets.leading : geo.safeAreaInsets.trailing
        let stripWidth = max(Self.sidebarWidth, cameraInset)

        return ZStack(alignment: cameraLeading ? .leading : .trailing) {
            // Stops at the sidebar rather than running under it: a fader
            // half behind a button is one that cannot be grabbed by its
            // bottom half.
            channelGrid
                .padding(edge, stripWidth)

            // Not while editing banks: the strips behind are then part of
            // the job, so they stay reachable.
            if openPanel != nil && !(openPanel == .bank && bankEditing) {
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
                    .padding(edge, stripWidth)
                    .transition(.move(edge: cameraLeading ? .leading : .trailing))
            }

            landscapeSidebar(cameraLeading: cameraLeading)
                .frame(width: stripWidth)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // The strips take the full height of the screen: full screen has
        // hidden the status bar, and the home indicator is hidden too, so
        // the bands the safe area keeps for them top and bottom are bare.
        // The camera's side is the control strip's to fill - see
        // stripWidth.
        .ignoresSafeArea(edges: [.top, .bottom, edge])
        // The Mute buttons now sit at the bottom edge, where a quick
        // press or a fader pulled all the way down could otherwise be
        // taken for the swipe home. Deferred, that swipe needs a second
        // go - the first only brings the indicator back.
        .defersSystemGestures(on: .bottom)
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

            SosButton(compact: true)
                .padding(.top, 6)

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
        .frame(maxWidth: .infinity, maxHeight: .infinity)
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
                        landscapeBankRows
                    }
                }
                .padding(.horizontal, 12)
                .padding(.bottom, 12)
            }

            if panel == .bank && model.personalizationAllowed {
                BankEditRow(
                    editing: bankEditing,
                    onAddChannels: selectedBankEditable ? { showBankChannels = true } : nil,
                    onRename: selectedBankEditable ? startRename : nil
                ) { setBankEditing(!bankEditing) }
            }
        }
        .frame(width: Self.panelWidth)
        .frame(maxHeight: .infinity)
        .background(Color.clmixSurface.ignoresSafeArea())
    }

    /// The banks as landscape's list, with edit mode's drag, "+" row and
    /// "New bank" placeholder - the portrait grid's (BankPanelView) in a
    /// single column.
    private var landscapeBankRows: some View {
        VStack(spacing: 8) {
            ForEach(Array(model.banks.enumerated()), id: \.element) { index, bank in
                let row = panelRow(bank, selected: bank == model.selectedBank) {
                    if !bankEditing {
                        withAnimation(panelAnimation) { openPanel = nil }
                    }
                    model.selectBank(bank)
                }

                if bankEditing {
                    row.onDrag {
                        draggingPanelBank = index
                        return reorderItemProvider("bank:\(index)")
                    }
                } else {
                    row
                }
            }

            if bankEditing {
                if newBankPlaceholder {
                    Text("New bank")
                        .italic()
                        .foregroundStyle(Color.clmixPrimary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 14)
                        .frame(height: 46)
                        .background(AddTileBackground())
                }

                Button(action: startNewBank) {
                    Image(systemName: "plus")
                        .font(.system(size: 18, weight: .bold))
                        .foregroundStyle(Color.clmixPrimary)
                        .frame(maxWidth: .infinity)
                        .frame(height: 46)
                        .background(AddTileBackground())
                }
                .accessibilityLabel("Add bank")
            }
        }
        .overlay(alignment: .topLeading) {
            if let target = panelBankDropTarget {
                GeometryReader { geo in
                    DropLine(vertical: false, length: geo.size.width)
                        .offset(y: max(CGFloat(target) * Self.panelRowPitch - 5.5, 0))
                }
            }
        }
        .onDrop(
            of: [.text],
            delegate: InsertionDropDelegate(
                isActive: bankEditing && draggingPanelBank != nil,
                insertionAt: { point in
                    min(max(Int((point.y / Self.panelRowPitch).rounded()), 0), model.banks.count)
                },
                target: $panelBankDropTarget,
                onDrop: { to in
                    if let from = draggingPanelBank { model.moveBank(from: from, to: to) }
                    draggingPanelBank = nil
                }
            )
        )
    }

    private func setBankEditing(_ on: Bool) {
        guard !on || model.personalizationAllowed else { return }
        guard bankEditing != on else { return }

        bankEditing = on

        if on {
            model.requestCustomBanks()
        } else {
            newBankPlaceholder = false
            showBankChannels = false
            channelDropTarget = nil
            panelBankDropTarget = nil
        }
    }

    /// Whether the bank on screen is one of this account's own, which is
    /// what the "+" channel adds to.
    private var selectedBankEditable: Bool {
        model.customBanks.contains { $0.name == model.selectedBank }
    }

    /// The "+" bank: a "New bank" tile goes into the grid straight away
    /// and the name is asked for.
    private func startNewBank() {
        model.requestCustomBanks()
        newBankName = ""
        newBankError = nil
        newBankPlaceholder = true
        askingBankName = true
    }

    private func finishNewBank() {
        let name = newBankName.trimmingCharacters(in: .whitespaces)

        guard !name.isEmpty else {
            newBankPlaceholder = false
            return
        }

        if let error = model.createBank(named: name) {
            // Asked again with the reason, the tile still waiting - once
            // this alert has finished going away.
            newBankError = error
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(400))
                askingBankName = true
            }
            return
        }

        newBankPlaceholder = false
    }

    private func startRename() {
        renameText = model.selectedBank ?? ""
        renameError = nil
        renamingBank = true
    }

    private func finishRename() {
        guard let current = model.selectedBank else { return }

        if let error = model.renameBank(from: current, to: renameText) {
            renameError = error
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(400))
                renamingBank = true
            }
        }
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
        HStack(spacing: 0) {
            // Edit mode's "+" strip, pinned ahead of the row rather than
            // scrolled with it so it is always there.
            if bankEditing && selectedBankEditable {
                AddChannelTile { showBankChannels = true }
            }

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
                        .overlay {
                            // While editing, the strips are things to
                            // arrange rather than mix on: this takes the
                            // touches before a fader can. A swipe still
                            // scrolls the row, and a hold picks a strip up.
                            if bankEditing {
                                Color.white.opacity(0.001)
                                    .onDrag {
                                        draggingChannel = index
                                        return reorderItemProvider("channel:\(index)")
                                    }
                            }
                        }
                    }
                }
                .overlay(alignment: .topLeading) {
                    if let target = channelDropTarget {
                        GeometryReader { geo in
                            DropLine(length: geo.size.height - 8)
                                .offset(x: max(CGFloat(target) * Self.stripPitch - 1.5, 0), y: 4)
                        }
                    }
                }
                .onDrop(
                    of: [.text],
                    delegate: InsertionDropDelegate(
                        isActive: bankEditing && draggingChannel != nil,
                        insertionAt: { point in
                            min(
                                max(Int((point.x / Self.stripPitch).rounded()), 0),
                                model.channels.count
                            )
                        },
                        target: $channelDropTarget,
                        onDrop: { to in
                            if let from = draggingChannel { model.moveChannel(from: from, to: to) }
                            draggingChannel = nil
                        }
                    )
                )
                .padding(10)
                .frame(maxHeight: .infinity, alignment: .top)
            }
            .frame(maxHeight: .infinity)
        }
    }

    /// [menuDocked]: the menu is already out down the left (an iPad on
    /// its side), so there is no button to open it.
    private func topBar(menuDocked: Bool) -> some View {
        HStack(spacing: 10) {
            if !menuDocked {
                Button {
                    showMenu = true
                } label: {
                    Image(systemName: "line.3.horizontal")
                        .font(.system(size: 18))
                        .frame(width: 40, height: 40)
                }
            }

            SosButton()

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

            // A tablet has the room for the banks themselves here, in an
            // outlined box between the menu and Fine. On a phone this is
            // empty, which keeps Fine and the bank toggle pinned to the
            // right edge.
            if LargeScreen.isActive {
                bankPanel(inTopBar: true)
                    .padding(.vertical, 2)
            } else {
                Spacer(minLength: 0)
            }

            fineButton
            // A tablet keeps its banks out, so there is nothing to toggle.
            if !LargeScreen.isActive { bankToggle }

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
        // Still open for an account that can make banks: an empty panel
        // with its "+" is where the first one gets made.
        .disabled(model.banks.isEmpty && !model.personalizationAllowed)
        .opacity(model.banks.isEmpty && !model.personalizationAllowed ? 0.35 : 1)
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
            // This account's picture and name - see ProfileView.
            ProfileHeader {
                dismissMenu()
                showProfile = true
            }

            // Channels and AUX, for an account holding Mixer Control -
            // this is the AUX one. Everyone else has only the aux screens.
            if model.mixerControlAllowed {
                MenuModeButtons(current: .aux) {
                    dismissMenu()
                    model.showChannels()
                }
            }

            // Held down at the bottom, in thumb reach - in the landscape
            // panel just as much as in the sheet, since the hand holding
            // the phone is at the bottom either way.
            Spacer()

            // The chat every phone shares with the sound engineer - see
            // ChatView. Open to every account.
            tonalButton("Chat") {
                dismissMenu()
                showChat = true
            }
            .padding(.horizontal, 14)
            .padding(.top, 10)

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

            // Log Out ends the session outright. On its own at the bottom,
            // well away from Channels and AUX at the top, which only move
            // between screens.
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
