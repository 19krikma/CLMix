import SwiftUI

/// Full Mixer Control: the console's own channel faders, pans and mutes.
///
/// The same strips the aux screen uses, riding different parameters - the
/// socket is put into mixer mode on the way in (AppModel.enterMixerControl)
/// and from then on every level/pan/mute write lands on the channel itself
/// rather than on one aux's sends. Where an account holding the
/// permission lands after login, with the aux screens a slide away behind
/// the menu's AUX; the server checks the permission again on every write.
///
/// What is deliberately absent, because it belongs to mixing one send:
/// the aux picker sheet and presets. Each strip also carries an EQ button
/// under its name, which opens that channel's EQ full screen (EqView).
/// Mirrors Android's MixerControlActivity.
struct MixerControlView: View {
    @EnvironmentObject var model: AppModel
    // Compact on a phone on its side - the strips then put their EQ,
    // dynamics and insert buttons beside the fader (sideControls).
    @Environment(\.verticalSizeClass) private var verticalSizeClass

    /// The channel input sheet's height: the input stage, then the delay
    /// section under it. It scrolls where the screen is shorter.
    private static let inputSheetHeight: CGFloat = 590

    @State private var banksExpanded = false
    @State private var showMenu = false
    @State private var showProfile = false
    @State private var showChat = false
    @State private var showFeedback = false
    // Which channel's input stage is open, by number rather than by
    // value: the sheet has to read the live channel off the model on
    // every push, not a copy captured when it was opened.
    @State private var openInput: OpenChannel?
    // A multi, DCA or bus being renamed on the desk - its heading opens
    // this instead of an input sheet. Mirrors Android's
    // MixerControlActivity.showStripRenameDialog.
    @State private var renameStrip: ChannelState?
    @State private var renameDraft = ""
    // Which channel's Inserts sheet is open - see insertSheet(for:).
    @State private var openInsert: OpenInsert?

    /// The open input sheet's channel number, wrapped only so `.sheet`
    /// has something Identifiable to key off.
    private struct OpenChannel: Identifiable {
        let id: Int
    }

    /// An Inserts sheet: which channel, and which insert ("A" or "B").
    private struct OpenInsert: Identifiable {
        let channel: Int
        let slot: String
        var id: String { "\(channel)-\(slot)" }
    }

    // As the aux screen: short enough to read as a control being
    // operated mid-show rather than as a screen transition.
    private let bankPanelAnimation = Animation.easeOut(duration: 0.1)

    var body: some View {
        GeometryReader { geo in
            // Phones only, as on the aux screen: a tablet on its side
            // still has the height for the portrait layout.
            if geo.size.width > geo.size.height && !LargeScreen.isActive {
                landscapeBody(geo)
            } else {
                portraitBody(geo)
            }
        }
        .background(Color.clmixBackground.ignoresSafeArea())
        .navigationBarHidden(true)
        .sheet(isPresented: $showMenu) { menuSheet }
        .sheet(isPresented: $showProfile) { ProfileView() }
        .sheet(isPresented: $showChat) { ChatView() }
        .sheet(isPresented: $showFeedback) { FeedbackView() }
        .sheet(item: $openInput) { open in inputSheet(for: open.id) }
        .sheet(item: $openInsert) { open in insertSheet(for: open.channel, slot: open.slot) }
        .alert(
            "Rename \(renameStrip?.label ?? "")",
            isPresented: Binding(
                get: { renameStrip != nil },
                set: { if !$0 { renameStrip = nil } }
            ),
            presenting: renameStrip
        ) { strip in
            TextField("Name", text: $renameDraft)
                .textInputAutocapitalization(.characters)
            Button("Save") {
                // The server's own MAX_CHANNEL_NAME cap.
                let name = String(
                    renameDraft.trimmingCharacters(in: .whitespacesAndNewlines).prefix(32)
                )
                if !name.isEmpty { model.setName(channel: strip.channel, name: name) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text("Renames it on the mixer, for every surface.")
        }
        // The EQ is its own full-screen, landscape view rather than a
        // sheet: a frequency graph needs the width. AppModel turns the
        // screen on its side as it opens.
        .fullScreenCover(isPresented: eqBinding) { EqView().environmentObject(model) }
        .fullScreenCover(isPresented: dynBinding) { DynamicsView().environmentObject(model) }
        // A bank switch can take the open channel off screen entirely,
        // leaving a sheet acting on a strip that is no longer there.
        .onChange(of: model.selectedBank) { _, _ in
            openInput = nil
            openInsert = nil
        }
        // Which side the camera is on, for the landscape sidebar - see
        // MixerView, which this mirrors.
        .onAppear {
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
            readInterfaceOrientation()
        }
        .onDisappear {
            UIDevice.current.endGeneratingDeviceOrientationNotifications()
        }
        .onReceive(
            NotificationCenter.default.publisher(for: UIDevice.orientationDidChangeNotification)
        ) { _ in
            switch UIDevice.current.orientation {
            case .landscapeLeft: cameraOnLeft = true
            case .landscapeRight: cameraOnLeft = false
            default: break
            }
        }
    }

    private func portraitBody(_ geo: GeometryProxy) -> some View {
            let menuDocked = LargeScreen.docksMenu(geo.size)

            return HStack(spacing: 0) {
                // As MixerView: an iPad on its side keeps the menu out.
                if menuDocked {
                    menuContent
                        .frame(width: LargeScreen.menuWidth)
                        .frame(maxHeight: .infinity)
                        .background(Color.clmixSurface)
                }

                VStack(spacing: 0) {
                    topBar(menuDocked: menuDocked)

                    // The bank picker floats over the strips rather than
                    // sitting above them: opening it covers the top of the
                    // faders instead of squeezing every one of them
                    // shorter, which leaves room for a whole surface's
                    // worth of banks, every layer.
                    ZStack(alignment: .top) {
                        channelGrid

                        if banksExpanded && !LargeScreen.isActive {
                            bankPanel(inTopBar: false)
                                .background(Color.clmixSurface)
                                .shadow(color: .black.opacity(0.4), radius: 6, y: 3)
                                .transition(.opacity)
                        }
                    }

                    SnapshotBar(snapshot: model.liveSnapshot)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Landscape sidebar (as MixerView's, with the menu and the
    // banks - there is no aux list or SOS here)

    /// Which side panel the landscape sidebar has out, if any.
    @State private var openPanel: LandscapePanel?

    private enum LandscapePanel { case menu, bank }

    /// Which side the camera is on with the phone on its side - see
    /// MixerView.cameraOnLeft for why it is tracked rather than measured.
    @State private var cameraOnLeft = true

    private static let sidebarWidth: CGFloat = 58
    private static let panelWidth: CGFloat = 300
    private let panelAnimation = Animation.easeOut(duration: 0.18)

    private func openChat() {
        dismissMenu()
        openInput = nil
        openInsert = nil
        showChat = true
    }

    /// Puts the menu away wherever it is - the sheet, or the panel.
    private func dismissMenu() {
        showMenu = false
        withAnimation(panelAnimation) { openPanel = nil }
    }

    /// The top bar and the snapshot line fold away and what the bar held
    /// - menu, banks, Fine - moves into a strip down the camera's edge,
    /// which is dead screen with the phone on its side. The strips get
    /// the whole height.
    private func landscapeBody(_ geo: GeometryProxy) -> some View {
        let cameraLeading = cameraOnLeft
        let edge: Edge.Set = cameraLeading ? .leading : .trailing
        let cameraInset = cameraLeading ? geo.safeAreaInsets.leading : geo.safeAreaInsets.trailing
        let stripWidth = max(Self.sidebarWidth, cameraInset)

        return ZStack(alignment: cameraLeading ? .leading : .trailing) {
            // Stops at the sidebar rather than running under it.
            channelGrid
                .padding(edge, stripWidth)

            if openPanel != nil {
                // Catches the tap that closes the panel, over the strips
                // so dismissing it cannot also move a fader.
                Color.black.opacity(0.6)
                    .ignoresSafeArea()
                    .onTapGesture { withAnimation(panelAnimation) { openPanel = nil } }
            }

            if let panel = openPanel {
                landscapePanel(panel)
                    .padding(edge, stripWidth)
                    .transition(.move(edge: cameraLeading ? .leading : .trailing))
            }

            landscapeSidebar(cameraLeading: cameraLeading)
                .frame(width: stripWidth)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .ignoresSafeArea(edges: [.top, .bottom, edge])
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

            // Chat, one tap away with the phone on its side - it is in
            // the menu too.
            Button {
                openChat()
            } label: {
                Image(systemName: "message")
                    .font(.system(size: 20, weight: .medium))
                    .frame(width: 44, height: 44)
            }
            .foregroundStyle(Color.primary)
            .accessibilityLabel("Chat")
            .padding(.top, 6)

            // The middle of the strip, left empty on purpose: it is the
            // stretch the camera sits in.
            Spacer()

            Button {
                withAnimation(panelAnimation) {
                    openPanel = openPanel == .bank ? nil : .bank
                }
            } label: {
                VStack(spacing: 2) {
                    Image(systemName: cameraLeading ? "chevron.right" : "chevron.left")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(Color.clmixPrimary)
                    Text("BANK")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(Color.primary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
            }

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

    private func landscapePanel(_ panel: LandscapePanel) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(panel == .menu ? "MENU" : "BANK")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
                .padding(.horizontal, 16)
                .padding(.top, 14)
                .padding(.bottom, panel == .bank ? 8 : 0)

            if panel == .menu {
                menuContent
            } else {
                // Every layer of the surface, scrolling if there are more
                // than fit.
                ScrollView {
                    bankPanel(inTopBar: false)
                }
            }
        }
        .frame(width: Self.panelWidth)
        .frame(maxHeight: .infinity)
        .background(Color.clmixSurface.ignoresSafeArea())
    }

    /// The starting answer, for a screen that opens with the phone
    /// already sideways - see MixerView.readInterfaceOrientation.
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

    /// As MixerView: under the bar on a phone, in its middle on a tablet.
    /// The surface layout, layer by layer, where the server sent one; the
    /// plain bank names from one too old to.
    @ViewBuilder
    private func bankPanel(inTopBar: Bool) -> some View {
        if model.bankLayers.isEmpty {
            BankPanelView(
                banks: model.banks,
                selectedBank: model.selectedBank,
                onSelect: { pick($0, inTopBar: inTopBar) },
                inTopBar: inTopBar
            )
        } else {
            LayeredBankPanelView(
                layers: model.bankLayers,
                selectedId: model.selectedBank,
                inline: inTopBar,
                onPick: { pick($0.id, inTopBar: inTopBar) }
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(inTopBar ? Color.clmixDivider : .clear, lineWidth: 1)
            )
        }
    }

    private func pick(_ bank: String, inTopBar: Bool) {
        if !inTopBar {
            withAnimation(bankPanelAnimation) { banksExpanded = false }
            withAnimation(panelAnimation) { openPanel = nil }
        }
        model.selectBank(bank)
    }

    private var eqBinding: Binding<Bool> {
        Binding(
            get: { model.eqChannel != nil },
            set: { if !$0 { model.closeEq() } }
        )
    }

    private var dynBinding: Binding<Bool> {
        Binding(
            get: { model.dynChannel != nil },
            set: { if !$0 { model.closeDynamics() } }
        )
    }

    private var channelGrid: some View {
        // As the aux screen: the model is read here and handed down as
        // values, so a strip rebuilds only when its own state moves - see
        // ChannelStripView.
        ScrollView(.horizontal) {
            HStack(alignment: .top, spacing: 0) {
                // Each strip where the desk has it; an empty fader is a
                // strip's width of nothing.
                ForEach(Array(model.mixerStrips.enumerated()), id: \.element.id) { index, channel in
                    if channel.isGap {
                        Color.clear.frame(width: ChannelStripView.width)
                    } else {
                        ChannelStripView(
                            channel: channel,
                            fineMode: model.fineMode,
                            alternate: index % 2 == 1,
                            // The console's own numbering, above each name -
                            // the whole console is in reach here, and the
                            // number is how the desk itself refers to a strip.
                            showChannelNumber: true,
                            // Small, beside the name, leaving the fader row to
                            // the fader.
                            meterBesideName: true,
                            panSupported: model.panSupported,
                            muteOffered: model.muteOffered,
                            onLevel: { model.setLevel(channel: channel.channel, db: $0) },
                            onPan: { model.setPan(channel: channel.channel, pan: $0) },
                            onMute: { model.setMute(channel: channel.channel, muted: $0) },
                            onOpenInput: { strip in
                                if strip.isInput {
                                    openInput = OpenChannel(id: strip.channel)
                                } else {
                                    renameDraft = strip.name
                                    renameStrip = strip
                                }
                            },
                            onOpenEq: { model.openEq(channel: $0.channel) },
                            onOpenDynamics: { model.openDynamics(channel: $0.channel, comp: $1) },
                            // An input's opens the Inserts sheet; a bus's has
                            // only in/out, so it flips on the spot.
                            onOpenInsert: { strip, slot in
                                if strip.isInput {
                                    openInsert = OpenInsert(channel: strip.channel, slot: slot)
                                } else {
                                    model.toggleBusInsert(channel: strip.channel, slot: slot)
                                }
                            },
                            onHardMute: { model.setHardMute(channel: channel.channel, muted: $0) },
                            sideControls: verticalSizeClass == .compact
                        )
                        .equatable()
                    }
                }
            }
            .padding(10)
            .frame(maxHeight: .infinity, alignment: .top)
        }
        .frame(maxHeight: .infinity)
    }

    /// Resolved out of the model on every push rather than captured when
    /// the sheet was opened, so what it shows is the console's live
    /// answer for that channel.
    /// The Inserts sheet, read off the model on every push like the
    /// input sheet below.
    @ViewBuilder
    private func insertSheet(for number: Int, slot: String) -> some View {
        if let channel = model.channels.first(where: { $0.channel == number }) {
            InsertSheet(channel: channel, slot: slot) { ch, slot, param, value in
                model.setInsert(channel: ch, slot: slot, param: param, value: value)
            }
            .presentationDetents([.height(270)])
        } else {
            Color.clmixSurface
                .presentationDetents([.height(270)])
        }
    }

    @ViewBuilder
    private func inputSheet(for number: Int) -> some View {
        if let channel = model.channels.first(where: { $0.channel == number }) {
            ChannelInputSheet(
                channel: channel,
                onGainChanged: { model.setGain(channel: $0, gain: $1) },
                onTrimChanged: { model.setTrim(channel: $0, trim: $1) },
                onPhantomChanged: { model.setPhantom(channel: $0, phantom: $1) },
                onAltGainChanged: { model.setAltGain(channel: $0, gain: $1) },
                onAltPhantomChanged: { model.setAltPhantom(channel: $0, phantom: $1) },
                onAltInChanged: { model.setAltIn(channel: $0, altIn: $1) },
                onPhaseChanged: { model.setPhase(channel: $0, phase: $1) },
                onDelayChanged: { model.setDelay(channel: $0, delayMs: $1) },
                onDelayOnChanged: { model.setDelayOn(channel: $0, on: $1) },
                onFineDelayChanged: { model.setFineDelay(channel: $0, samples: $1) },
                gainRange: model.gainRange,
                trimRange: model.trimRange,
                onNameChanged: { model.setName(channel: $0, name: $1) }
            )
            .presentationDetents([.height(Self.inputSheetHeight)])
        } else {
            // The channel went away under the sheet - a bank switch
            // usually, which also closes it. Anything rather than a
            // half-built sheet in the frame or two before that lands.
            Color.clmixSurface
                .presentationDetents([.height(Self.inputSheetHeight)])
        }
    }

    /// [menuDocked]: as MixerView, no button for a menu already out.
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

            // There is no aux bar along the bottom naming what is being
            // mixed, as there is on the aux screen - so the one thing
            // that says these faders are the console's own is here.
            Text("Mixer Control")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(Color.clmixOnSurface)

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

            // The banks, on a tablet - see MixerView.topBar.
            if LargeScreen.isActive {
                bankPanel(inTopBar: true)
                    .padding(.vertical, 2)
            } else {
                Spacer(minLength: 0)
            }

            fineButton
            // A tablet keeps its banks out, so there is nothing to toggle.
            if !LargeScreen.isActive { bankToggle }

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

    /// Presets are per-aux-send, so the aux screen's Presets button has
    /// nothing to act on here and is deliberately absent.
    private var menuSheet: some View {
        menuContent
            .frame(maxWidth: .infinity)
            .background(Color.clmixSurface)
            .presentationDetents([.medium])
    }

    /// The menu itself: in a sheet, or docked down the left of an iPad on
    /// its side. One copy, as on the aux screen.
    private var menuContent: some View {
        VStack(alignment: .leading, spacing: 0) {
            // This account's picture and name - see ProfileView.
            ProfileHeader {
                dismissMenu()
                openInput = nil
                openInsert = nil
                showProfile = true
            }

            // Channels and AUX - this is the Channels one. Only an account
            // holding Mixer Control ever reaches this screen.
            MenuModeButtons(current: .channels) {
                dismissMenu()
                openInput = nil
                openInsert = nil
                model.showAux()
            }

            Spacer()

            // The chat every phone shares with the sound engineer - see
            // ChatView. Open to every account, as on the aux screen.
            Button {
                openChat()
            } label: {
                Text("Chat")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
            }
            .foregroundStyle(Color.clmixOnMuteInactive)
            .background(Color.clmixMuteInactive)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .padding(.horizontal, 14)
            .padding(.top, 10)

            // Anonymous feedback to the developer - see FeedbackView.
            // Needs no mixer server, so it is open to every account.
            Button {
                dismissMenu()
                openInput = nil
                openInsert = nil
                showFeedback = true
            } label: {
                Text("Feedback")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
            }
            .foregroundStyle(Color.clmixOnMuteInactive)
            .background(Color.clmixMuteInactive)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .padding(.horizontal, 14)
            .padding(.top, 10)

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
}
