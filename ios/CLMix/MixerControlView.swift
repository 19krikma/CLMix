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

    @State private var banksExpanded = false
    @State private var showMenu = false
    @State private var showProfile = false
    // Which channel's input stage is open, by number rather than by
    // value: the sheet has to read the live channel off the model on
    // every push, not a copy captured when it was opened.
    @State private var openInput: OpenChannel?

    /// The open input sheet's channel number, wrapped only so `.sheet`
    /// has something Identifiable to key off.
    private struct OpenChannel: Identifiable {
        let id: Int
    }

    // As the aux screen: short enough to read as a control being
    // operated mid-show rather than as a screen transition.
    private let bankPanelAnimation = Animation.easeOut(duration: 0.1)

    var body: some View {
        GeometryReader { geo in
            let menuDocked = LargeScreen.docksMenu(geo.size)

            HStack(spacing: 0) {
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
        .background(Color.clmixBackground.ignoresSafeArea())
        .navigationBarHidden(true)
        .sheet(isPresented: $showMenu) { menuSheet }
        .sheet(isPresented: $showProfile) { ProfileView() }
        .sheet(item: $openInput) { open in inputSheet(for: open.id) }
        // The EQ is its own full-screen, landscape view rather than a
        // sheet: a frequency graph needs the width. AppModel turns the
        // screen on its side as it opens.
        .fullScreenCover(isPresented: eqBinding) { EqView().environmentObject(model) }
        .fullScreenCover(isPresented: dynBinding) { DynamicsView().environmentObject(model) }
        // A bank switch can take the open channel off screen entirely,
        // leaving a sheet acting on a strip that is no longer there.
        .onChange(of: model.selectedBank) { _, _ in openInput = nil }
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
                ForEach(Array(model.channels.enumerated()), id: \.element.id) { index, channel in
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
                        onOpenInput: { openInput = OpenChannel(id: $0.channel) },
                        onOpenEq: { model.openEq(channel: $0.channel) },
                        onOpenDynamics: { model.openDynamics(channel: $0.channel, comp: $1) }
                    )
                    .equatable()
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
                gainRange: model.gainRange,
                trimRange: model.trimRange,
                onNameChanged: { model.setName(channel: $0, name: $1) }
            )
            .presentationDetents([.height(440)])
        } else {
            // The channel went away under the sheet - a bank switch
            // usually, which also closes it. Anything rather than a
            // half-built sheet in the frame or two before that lands.
            Color.clmixSurface
                .presentationDetents([.height(440)])
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
                showMenu = false
                openInput = nil
                showProfile = true
            }

            // Channels and AUX - this is the Channels one. Only an account
            // holding Mixer Control ever reaches this screen.
            MenuModeButtons(current: .channels) {
                showMenu = false
                openInput = nil
                model.showAux()
            }

            Spacer()

            // Log Out ends the session outright. On its own at the bottom,
            // well away from Channels and AUX at the top, which only move
            // between screens.
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
    }
}
