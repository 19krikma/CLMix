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
    @Published var isConnecting = false
    @Published var auxes: [AuxBus] = []
    @Published var banks: [String] = []
    @Published var channels: [ChannelState] = []
    @Published var fineMode = false
    @Published var presetsAllowed = false
    @Published var presetNames: [String] = []
    @Published var discoveredServers: [DiscoveredServer] = []

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

    override init() {
        super.init()
        MixerClient.shared.delegate = self
    }

    func connect(host: String, port: Int, username: String, password: String) {
        pendingToken = nil
        pendingUsername = username
        pendingPassword = password
        isConnecting = true
        statusMessage = "Connecting..."
        MixerClient.shared.connect(host: host, port: port)
    }

    /// Silently resumes a previous session instead of making the user log
    /// in again every launch (mirrors Android's ConnectActivity.onCreate).
    /// Only runs when there's actually a saved token and a remembered
    /// server, and never tears down a live socket - so returning to the
    /// connect screen mid-session doesn't kill a perfectly good
    /// connection.
    func resumeSessionIfPossible() {
        guard !MixerClient.shared.isConnected, !isConnecting,
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
        MixerClient.shared.connect(host: host, port: port)
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
        MixerClient.shared.selectAux(aux.index)
        MixerClient.shared.requestBanks()
    }

    func selectBank(_ bank: String?) {
        MixerClient.shared.selectBank(bank)
    }

    func setLevel(channel: Int, db: Double) {
        MixerClient.shared.setLevel(channel: channel, db: db)
    }

    func setPan(channel: Int, pan: Double) {
        MixerClient.shared.setPan(channel: channel, pan: pan)
    }

    func requestPresets() {
        MixerClient.shared.requestPresets()
    }

    func savePreset(name: String, onSaved: @escaping () -> Void) {
        pendingPresetSaveCompletion = onSaved
        MixerClient.shared.savePreset(name: name)
    }

    func loadPreset(name: String, onLoaded: @escaping () -> Void) {
        pendingPresetLoadCompletion = onLoaded
        MixerClient.shared.loadPreset(name: name)
    }

    /// Revokes the session server-side and drops the saved token before
    /// disconnecting, so the connect screen comes back genuinely logged
    /// out rather than silently resuming on its next appearance.
    func logout() {
        // logout() closes the socket itself, once the revocation has
        // actually gone out on it.
        MixerClient.shared.logout(token: SessionStore.token())
        SessionStore.clear()

        pendingToken = nil
        pendingUsername = ""
        pendingPassword = ""
        channels = []
        auxes = []
        presetsAllowed = false
        presetNames = []
        isConnecting = false
        statusMessage = ""
        screen = .connect
    }
}

extension AppModel: MixerClientDelegate {
    func mixerDidConnect() {
        if let pendingToken {
            statusMessage = "Resuming session..."
            MixerClient.shared.login(token: pendingToken)
        } else {
            statusMessage = "Logging in..."
            MixerClient.shared.login(username: pendingUsername, password: pendingPassword)
        }
    }

    func mixerDidDisconnect() {
        isConnecting = false
        statusMessage = "Disconnected"
        screen = .connect
    }

    func mixerDidFail(message: String) {
        isConnecting = false
        statusMessage = "Error: \(message)"

        // Some errors are just protocol-level (e.g. "not permitted for
        // this aux") and don't mean the socket itself dropped.
        if !MixerClient.shared.isConnected {
            screen = .connect
        }
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

            statusMessage = "Connected"
            presetsAllowed = MixerClient.shared.presetsAllowed
            MixerClient.shared.requestAuxes()
            return
        }

        let wasTokenAttempt = pendingToken != nil
        pendingToken = nil

        if wasTokenAttempt {
            // The saved session is no longer valid - it aged past the
            // server's SESSION_TTL_SECONDS, was revoked, or the desktop
            // app restarted since it was issued. Drop it so the next
            // launch doesn't keep retrying a dead token, and fall back to
            // the ordinary login form already on screen.
            SessionStore.clear()
            statusMessage = message ?? ""
        } else {
            statusMessage = "Login failed: \(message ?? "Invalid username or password")"
        }

        MixerClient.shared.disconnect()
    }

    func mixerDidReceiveAuxes(_ auxes: [AuxBus]) {
        self.auxes = auxes
        screen = .auxList
    }

    func mixerDidReceiveBanks(_ banks: [String]) {
        self.banks = banks
    }

    func mixerDidReceiveLevels(aux: Int, channels: [ChannelState]) {
        guard case .mixer(let currentAux) = screen, currentAux.index == aux else { return }
        self.channels = channels
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
