import Foundation

enum AppScreen: Equatable {
    case connect
    case auxList
    case mixer(AuxBus)
}

/// Coordinates navigation and mixer state - the Android app spreads this
/// across ConnectActivity/AuxListActivity/MixerActivity, each holding its
/// own MixerClientListener; SwiftUI's single-window model collapses that
/// into one observable source of truth driving which screen is shown.
@MainActor
final class AppModel: NSObject, ObservableObject {
    @Published var screen: AppScreen = .connect
    @Published var statusMessage = ""
    // True while statusMessage describes a failure rather than progress -
    // ConnectView reads it to color the message red instead of secondary.
    @Published var statusIsError = false
    // True while the credentials on screen were rejected - the one thing
    // typing into either field clears (see clearError()). Mirrors
    // Android's ConnectActivity.credentialsRejected.
    @Published var credentialsRejected = false
    @Published var isConnecting = false
    // A failure reported on the Login button itself rather than the
    // message line - "Can't Reach Server" (the socket never opened) or
    // "No Snapshot Access" (a standing permissions problem). Holds for
    // buttonResultHoldSeconds and then clears itself. Mirrors Android's
    // ConnectActivity.showButtonResult/resetButton.
    @Published var buttonResultLabel: String?
    @Published var buttonResultIsRejection = false
    @Published var auxes: [AuxBus] = []
    @Published var banks: [String] = []
    // Which bank's channels are on screen. Lives here rather than in
    // MixerView so it survives the view being rebuilt, and so the
    // opening-bank logic in mixerDidReceiveBanks has one place to write
    // it. Null only before the first bank arrives, or on a console that
    // reports none at all - there is no "All" choice any more.
    @Published var selectedBank: String?
    @Published var channels: [ChannelState] = []
    @Published var fineMode = false
    @Published var presetsAllowed = false
    // Gates whether the Mute button is offered at all, mirroring the
    // server's own per-user check (which still applies regardless of
    // what is drawn). True by default: an account with no explicit
    // setting, and an older server that never sends the field, both mean
    // "allowed".
    @Published var muteAllowed = true
    @Published var presetNames: [String] = []
    @Published var discoveredServers: [DiscoveredServer] = []
    // True while the session is a local demo rather than a real console.
    // Drives the DEMO badge on the mixer screen, so there is never any
    // doubt about whether moves are reaching real hardware.
    // REMOVE WITH DEMO MODE.
    @Published var isDemo = false

    // Whichever session is live. Swapped to DemoMixer.shared by
    // enterDemoMode() and back on logout or a real connect.
    // REMOVE WITH DEMO MODE: fold back to MixerClient.shared throughout.
    private var backend: MixerBackend = MixerClient.shared

    // Stashed from the form (or a saved session) at connect time so
    // mixerDidConnect knows how to log in once the socket is open, without
    // persisting the password itself anywhere. Exactly one of
    // pendingPassword/pendingToken is used per attempt.
    private var pendingUsername = ""
    private var pendingPassword = ""
    private var pendingToken: String?

    private let mdnsDiscovery = MdnsDiscovery()

    // Written by ConnectView's @AppStorage bindings - read back here so
    // the resume path can reach the last-used server without ConnectView
    // having to drive it.
    private enum StorageKey {
        static let host = "clmix.host"
        static let port = "clmix.port"
    }

    // Fulfilled once the matching preset_saved/preset_loaded reply
    // arrives, so the sheet that requested it can dismiss/confirm itself -
    // mirrors Android's PresetSaveBottomSheet/PresetLoadBottomSheet
    // dismissing from MixerActivity's onPresetSaved/onPresetLoaded.
    private var pendingPresetSaveCompletion: (() -> Void)?
    private var pendingPresetLoadCompletion: (() -> Void)?

    // How long a failure stays on the Login button before it turns back
    // into "Login" - mirrors Android's ConnectActivity.RESULT_HOLD_MS.
    private let buttonResultHoldSeconds: UInt64 = 5
    private var buttonResultTask: Task<Void, Never>?

    // Until a push agrees with what a mute tap asked for, pushes for that
    // channel's mute are ignored, so an in-flight one describing the
    // pre-tap state can't flip the button back and forth. After
    // muteConfirmTimeout without agreement the request is assumed lost -
    // the console's own state wins again. Resolved in
    // mixerDidReceiveLevels, mirroring Android's
    // ChannelAdapter.effectiveMuted (resolved on every push/rebind there).
    private struct PendingMute {
        let expected: Bool
        let sentAt: Date
    }
    private var pendingMutes: [Int: PendingMute] = [:]
    private let muteConfirmTimeout: TimeInterval = 2

    /// The aux being mixed, resolved against the live aux list so its
    /// `stereo` flag is whatever the server last reported rather than
    /// whatever was captured when the screen was opened.
    var currentAux: AuxBus? {
        guard case .mixer(let aux) = screen else { return nil }
        return auxes.first { $0.index == aux.index } ?? aux
    }

    /// A mono aux has no pan axis, so the Pan button comes off the strip
    /// entirely - the same thing the desktop does with its pan slider.
    /// An unknown aux (an older server, or before the list arrives) is
    /// treated as stereo, which is how it always behaved.
    var panSupported: Bool { currentAux?.stereo ?? true }

    override init() {
        super.init()
        backend.delegate = self
    }

    func connect(host: String, port: Int, username: String, password: String) {
        leaveDemoMode()
        pendingToken = nil
        pendingUsername = username
        pendingPassword = password
        resetButton()
        isConnecting = true
        statusIsError = false
        credentialsRejected = false
        statusMessage = "Connecting..."
        backend.connect(host: host, port: port)
    }

    /// Starts a local demo session: no server, no console, no network. Goes
    /// through exactly the same connect -> login -> auxes sequence a real
    /// session does (see DemoMixer), so nothing downstream of here knows the
    /// difference.
    ///
    /// REMOVE WITH DEMO MODE.
    func enterDemoMode() {
        // Drop the outgoing backend's delegate as well as closing it: a
        // socket teardown can still deliver a didClose callback afterwards,
        // and mixerDidDisconnect would bounce the demo straight back to the
        // connect screen.
        backend.disconnect()
        backend.delegate = nil
        backend = DemoMixer.shared
        backend.delegate = self
        isDemo = true

        pendingToken = nil
        pendingUsername = "demo"
        pendingPassword = "demo"
        resetButton()
        isConnecting = true
        statusIsError = false
        credentialsRejected = false
        statusMessage = "Starting demo..."
        backend.connect(host: "demo", port: 0)
    }

    /// REMOVE WITH DEMO MODE.
    private func leaveDemoMode() {
        guard isDemo else { return }
        backend.disconnect()
        backend.delegate = nil
        backend = MixerClient.shared
        backend.delegate = self
        isDemo = false
    }

    /// Reports a failure on the Login button itself: the spinner gives
    /// way to a short label, which holds briefly and then turns back into
    /// "Login" so the user can just try again. Mirrors Android's
    /// ConnectActivity.showButtonResult.
    private func showButtonResult(_ label: String, isRejection: Bool) {
        isConnecting = false
        buttonResultTask?.cancel()

        buttonResultLabel = label
        buttonResultIsRejection = isRejection

        buttonResultTask = Task { [weak self, buttonResultHoldSeconds] in
            try? await Task.sleep(nanoseconds: buttonResultHoldSeconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            self?.resetButton()
        }
    }

    private func resetButton() {
        buttonResultTask?.cancel()
        buttonResultTask = nil
        buttonResultLabel = nil
        buttonResultIsRejection = false
    }

    /// The one thing typing into either credential field does while an
    /// error is showing: clears both the message and the fields' rejected
    /// styling, without otherwise touching connection state. Mirrors
    /// Android's ConnectActivity.clearErrorOnType.
    func clearError() {
        guard credentialsRejected || statusIsError else { return }
        statusMessage = ""
        statusIsError = false
        credentialsRejected = false
    }

    /// Silently resumes a previous session instead of making the user log
    /// in again every launch (mirrors Android's ConnectActivity.onCreate).
    /// Only runs when there's actually a saved token and a remembered
    /// server, and never tears down a live socket - so returning to the
    /// connect screen mid-session doesn't kill a perfectly good
    /// connection.
    func resumeSessionIfPossible() {
        guard !backend.isConnected, !isConnecting,
              let token = SessionStore.token(),
              let host = UserDefaults.standard.string(forKey: StorageKey.host),
              !host.isEmpty,
              let port = Int(UserDefaults.standard.string(forKey: StorageKey.port) ?? "8765") else {
            return
        }

        pendingToken = token
        pendingPassword = ""
        isConnecting = true
        statusMessage = "Resuming session..."
        backend.connect(host: host, port: port)
    }

    func startDiscovery() {
        mdnsDiscovery.start(delegate: self)
    }

    func stopDiscovery() {
        mdnsDiscovery.stop()
        discoveredServers = []
    }

    func selectAux(_ aux: AuxBus) {
        channels = []
        screen = .mixer(aux)
        backend.selectAux(aux.index)
        backend.requestBanks()
    }

    /// Switches the mix being edited without leaving the mixer screen -
    /// what the aux bottom sheet does. The bank filter deliberately
    /// survives the switch, matching RemoteServer._handle (select_aux
    /// sets state["aux"] and leaves state["bank"] alone).
    func switchAux(_ aux: AuxBus) {
        guard case .mixer(let current) = screen, current.index != aux.index else { return }

        channels = []
        screen = .mixer(aux)
        backend.selectAux(aux.index)
    }

    func selectBank(_ bank: String) {
        guard bank != selectedBank else { return }
        selectedBank = bank
        backend.selectBank(bank)
    }

    func setLevel(channel: Int, db: Double) {
        backend.setLevel(channel: channel, db: db)
    }

    func setPan(channel: Int, pan: Double) {
        backend.setPan(channel: channel, pan: pan)
    }

    /// Flips the channel's mute button straight away rather than waiting
    /// for the console's echo to travel back through the desktop app's
    /// cache and the next push (~150ms at best, longer over a busy wifi) -
    /// but the server stays the authority on what's actually muted.
    /// Mirrors Android's ChannelAdapter mute-tap handling.
    func setMute(channel: Int, muted: Bool) {
        pendingMutes[channel] = PendingMute(expected: muted, sentAt: Date())
        if let index = channels.firstIndex(where: { $0.channel == channel }) {
            channels[index].muted = muted
        }
        backend.setMute(channel: channel, muted: muted)
    }

    func requestPresets() {
        backend.requestPresets()
    }

    func savePreset(name: String, onSaved: @escaping () -> Void) {
        pendingPresetSaveCompletion = onSaved
        backend.savePreset(name: name)
    }

    func loadPreset(name: String, onLoaded: @escaping () -> Void) {
        pendingPresetLoadCompletion = onLoaded
        backend.loadPreset(name: name)
    }

    /// Revokes the session server-side and drops the saved token before
    /// disconnecting, so the connect screen comes back genuinely logged
    /// out rather than silently resuming on its next appearance.
    func logout() {
        // logout() closes the socket itself, once the revocation has
        // actually gone out on it.
        backend.logout(token: SessionStore.token())

        // A demo session has no token to revoke, and the one in the Keychain
        // (if any) belongs to a real session started before the demo - so
        // leaving a demo must not log the user out of that. REMOVE WITH
        // DEMO MODE: the guard goes, the clear() stays.
        if !isDemo {
            SessionStore.clear()
        }
        leaveDemoMode()

        MeterCenter.shared.clear()

        pendingToken = nil
        pendingUsername = ""
        pendingPassword = ""
        channels = []
        auxes = []
        banks = []
        selectedBank = nil
        presetsAllowed = false
        muteAllowed = true
        presetNames = []
        isConnecting = false
        statusIsError = false
        credentialsRejected = false
        statusMessage = ""
        resetButton()
        screen = .connect
    }
}

extension AppModel: MixerClientDelegate {
    func mixerDidConnect() {
        if let pendingToken {
            statusMessage = "Resuming session..."
            backend.login(token: pendingToken)
        } else {
            statusMessage = "Logging in..."
            backend.login(username: pendingUsername, password: pendingPassword)
        }
    }

    func mixerDidDisconnect() {
        // Don't talk over a failure that just closed this socket itself -
        // the message line or the button is already reporting it, and
        // this callback arrives right behind that close. Mirrors
        // Android's ConnectActivity.onDisconnected.
        if statusIsError || buttonResultLabel != nil { return }

        MeterCenter.shared.clear()

        let wasMidSession = screen != .connect
        isConnecting = false
        statusMessage = wasMidSession ? "Disconnected" : ""
        credentialsRejected = false
        screen = .connect
    }

    func mixerDidFail(message: String) {
        // Still on the connect screen means this is part of the login
        // flow rather than a mid-session hiccup on the mixer/aux list -
        // mirrors Android's ConnectActivity owning onConnectionFailed/
        // onError only until it navigates away.
        let stillConnecting = screen == .connect

        if !backend.isConnected {
            if stillConnecting {
                // The socket never opened at all - bad address, server not
                // running, wrong network. The specific cause isn't
                // actionable from here, so the button says so plainly for
                // a moment and then offers itself again. Mirrors
                // Android's onConnectionFailed.
                statusMessage = ""
                statusIsError = false
                credentialsRejected = false
                showButtonResult("Can't Reach Server", isRejection: false)
            } else {
                MeterCenter.shared.clear()
                isConnecting = false
                statusIsError = true
                statusMessage = "Disconnected"
                screen = .connect
            }
            return
        }

        if stillConnecting {
            // Connected and logged in, but the next step - fetching the
            // aux list - came back rejected.
            if message == MixerClient.snapshotDenied {
                // A standing permissions problem, not a transient
                // failure: this account is scoped to a different snapshot
                // than the one live on the console right now. Called out
                // on the button, since retrying as-is will fail the same
                // way.
                statusMessage = ""
                statusIsError = false
                credentialsRejected = false
                showButtonResult("No Snapshot Access", isRejection: true)
            } else {
                // Anything else the server rejected is a full sentence
                // already, and too long for the button - it goes on the
                // message line instead.
                isConnecting = false
                statusIsError = true
                credentialsRejected = false
                statusMessage = message
            }
            backend.disconnect()
            return
        }

        // A preset request that will now never get its reply must not
        // leave the sheet that asked for it stuck on "Saving..." forever -
        // every preset rejection ("Preset not found", "Not permitted for
        // presets", "No aux selected") arrives here as a plain error.
        pendingPresetSaveCompletion?()
        pendingPresetSaveCompletion = nil
        pendingPresetLoadCompletion?()
        pendingPresetLoadCompletion = nil

        // A mid-session protocol error (e.g. "not permitted for this
        // aux") - shown in place, without navigating away or dropping the
        // connection.
        statusIsError = true
        statusMessage = message
    }

    func mixerDidReceiveLoginResult(ok: Bool, message: String?, token: String?) {
        isConnecting = false

        if ok {
            // The password has done its job - don't keep it in memory for
            // the rest of the app's lifetime when the token supersedes it.
            pendingPassword = ""
            pendingToken = nil

            if let token {
                SessionStore.save(token)
            }

            statusIsError = false
            credentialsRejected = false
            statusMessage = "Connected"
            presetsAllowed = backend.presetsAllowed
            muteAllowed = backend.muteAllowed
            backend.requestAuxes()
            return
        }

        let wasTokenAttempt = pendingToken != nil
        pendingToken = nil

        if wasTokenAttempt {
            // The saved session is no longer valid - it aged past the
            // server's SESSION_TTL_SECONDS, was revoked, or the desktop
            // app restarted since it was issued. Nothing on screen for the
            // user to correct, so this doesn't mark the fields - just
            // drop the dead token and fall back to the ordinary login
            // form already on screen.
            SessionStore.clear()
            statusIsError = false
            credentialsRejected = false
            statusMessage = ""
        } else {
            statusIsError = true
            credentialsRejected = true
            statusMessage = message ?? "Invalid username or password"
        }

        backend.disconnect()
    }

    func mixerDidReceiveAuxes(_ auxes: [AuxBus]) {
        self.auxes = auxes
        screen = .auxList
    }

    /// No synthetic "All" entry: every channel at once is a whole
    /// console's worth of strips, faders and meters for a phone that can
    /// show a dozen - the same reason the desktop dropped its own All
    /// button. The console's banks are the only choices. Mirrors
    /// Android's MixerActivity.onBanks.
    func mixerDidReceiveBanks(_ banks: [String]) {
        self.banks = banks

        if banks.isEmpty {
            // A console that reports no banks at all: fall back to every
            // channel rather than leaving the operator with an empty
            // screen. A fallback, not a choice - there is still nothing
            // to pick.
            if selectedBank != nil {
                selectedBank = nil
                backend.selectBank(nil)
            }
            return
        }

        // Whatever was showing before, else the console's own first
        // bank. Nothing selects itself here, so the opening bank has to
        // be asked for explicitly.
        let bank = selectedBank.flatMap { banks.contains($0) ? $0 : nil } ?? banks[0]

        if bank != selectedBank {
            selectedBank = bank
            backend.selectBank(bank)
        }
    }

    // Straight through to the meter views, deliberately not into any
    // @Published property - see MeterCenter for why.
    func mixerDidReceiveMeters(sequence: Int64, meters: [Int: MeterLevels]) {
        MeterCenter.shared.publish(sequence: sequence, frame: meters)
    }

    func mixerDidReceiveLevels(aux: Int, channels: [ChannelState]) {
        guard case .mixer(let currentAux) = screen, currentAux.index == aux else { return }

        // A bank switch (or aux switch) can drop channels that still have
        // a tap awaiting confirmation - without pruning, a stale entry
        // would sit here suppressing that channel's real state if it ever
        // came back. Mirrors Android's pendingMutes.retainAll.
        let newChannelSet = Set(channels.map(\.channel))
        pendingMutes = pendingMutes.filter { newChannelSet.contains($0.key) }

        let now = Date()
        self.channels = channels.map { channel in
            guard let pending = pendingMutes[channel.channel] else { return channel }

            let confirmed = channel.muted == pending.expected
            let timedOut = now.timeIntervalSince(pending.sentAt) >= muteConfirmTimeout

            if confirmed || timedOut {
                pendingMutes.removeValue(forKey: channel.channel)
                return channel
            }

            var held = channel
            held.muted = pending.expected
            return held
        }
    }

    func mixerDidReceivePresets(_ names: [String]) {
        presetNames = names
    }

    func mixerDidSavePreset(_ name: String) {
        pendingPresetSaveCompletion?()
        pendingPresetSaveCompletion = nil
    }

    func mixerDidLoadPreset(_ name: String) {
        pendingPresetLoadCompletion?()
        pendingPresetLoadCompletion = nil
    }
}

extension AppModel: MdnsDiscoveryDelegate {
    func mdnsDidFindServer(_ server: DiscoveredServer) {
        if let index = discoveredServers.firstIndex(where: { $0.id == server.id }) {
            discoveredServers[index] = server
        } else {
            discoveredServers.append(server)
        }
    }

    func mdnsDidLoseServer(id: String) {
        discoveredServers.removeAll { $0.id == id }
    }
}
