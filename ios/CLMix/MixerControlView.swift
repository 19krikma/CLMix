import SwiftUI

/// Full Mixer Control: the console's own channel faders, pans and mutes.
///
/// The same strips the aux screen uses, riding different parameters - the
/// socket is put into mixer mode on the way in (AppModel.enterMixerControl)
/// and from then on every level/pan/mute write lands on the channel itself
/// rather than on one aux's sends. Reached only from ControlChoiceView,
/// which is itself only offered to accounts holding the permission; the
/// server checks it again on every write.
///
/// What is deliberately absent, because it belongs to mixing one send:
/// the aux picker sheet and presets. Mirrors Android's
/// MixerControlActivity.
struct MixerControlView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var themeStore: ThemeStore
    @Environment(\.colorScheme) var systemColorScheme

    @State private var banksExpanded = false
    @State private var showMenu = false
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

    private var darkModeBinding: Binding<Bool> {
        Binding(
            get: { themeStore.isDarkMode ?? (systemColorScheme == .dark) },
            set: { themeStore.isDarkMode = $0 }
        )
    }

    var body: some View {
        VStack(spacing: 0) {
            topBar

            if banksExpanded {
                BankPanelView(
                    banks: model.banks,
                    selectedBank: model.selectedBank,
                    onSelect: { bank in
                        withAnimation(bankPanelAnimation) { banksExpanded = false }
                        model.selectBank(bank)
                    }
                )
                .transition(.opacity)
            }

            channelGrid
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.clmixBackground)
        .navigationBarHidden(true)
        .sheet(isPresented: $showMenu) { menuSheet }
        .sheet(item: $openInput) { open in inputSheet(for: open.id) }
        // A bank switch can take the open channel off screen entirely,
        // leaving a sheet acting on a strip that is no longer there.
        .onChange(of: model.selectedBank) { _, _ in openInput = nil }
    }

    private var channelGrid: some View {
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
                        onOpenInput: { openInput = OpenChannel(id: $0.channel) }
                    )
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
                onPhaseChanged: { model.setPhase(channel: $0, phase: $1) },
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

    private var topBar: some View {
        HStack(spacing: 10) {
            Button {
                showMenu = true
            } label: {
                Image(systemName: "line.3.horizontal")
                    .font(.system(size: 18))
                    .frame(width: 40, height: 40)
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

            Spacer(minLength: 0)

            fineButton
            bankToggle

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
        VStack(alignment: .leading, spacing: 0) {
            Spacer()

            Toggle("Dark mode", isOn: darkModeBinding)
                .padding(.horizontal, 14)
                .padding(.top, 16)

            // Hard mute assembles something the console has no single
            // control for on an input channel: the channel mute plus
            // every aux send dropped, so the channel leaves the monitors
            // as well as the room. Sits directly above the way out of
            // this screen, since both are decisions about leaving
            // something behind.
            Toggle("Hard mute", isOn: $model.hardMute)
                .padding(.horizontal, 14)
                .padding(.top, 10)

            Text("Mutes the channel everywhere - the room and every monitor mix.")
                .font(.system(size: 12))
                .foregroundStyle(Color.clmixOnSurfaceVariant)
                .padding(.horizontal, 14)
                .padding(.top, 4)

            // Back returns to the AUX Only / Mixer Control choice; Log Out
            // ends the session outright. Side by side because they are the
            // same kind of action at different depths.
            HStack(spacing: 12) {
                Button {
                    showMenu = false
                    openInput = nil
                    model.leaveMixerControl()
                } label: {
                    Text("Back")
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                }
                .foregroundStyle(Color.clmixOnMuteInactive)
                .background(Color.clmixMuteInactive)
                .clipShape(RoundedRectangle(cornerRadius: 10))

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
            }
            .padding(14)
        }
        .frame(maxWidth: .infinity)
        .background(Color.clmixSurface)
        .presentationDetents([.medium])
    }
}
