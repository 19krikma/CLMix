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

    private var pendingUsername = ""
    private var pendingPassword = ""

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
        pendingUsername = username
        pendingPassword = password
        isConnecting = true
        statusMessage = "Connecting..."
        MixerClient.shared.connect(host: host, port: port)
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

    func logout() {
        MixerClient.shared.disconnect()
        channels = []
        auxes = []
        presetsAllowed = false
        presetNames = []
        isConnecting = false
        screen = .connect
    }
}

extension AppModel: MixerClientDelegate {
    func mixerDidConnect() {
        statusMessage = "Logging in..."
        MixerClient.shared.login(username: pendingUsername, password: pendingPassword)
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

    func mixerDidReceiveLoginResult(ok: Bool, message: String?) {
        if ok {
            isConnecting = false
            statusMessage = "Connected"
            presetsAllowed = MixerClient.shared.presetsAllowed
            MixerClient.shared.requestAuxes()
        } else {
            isConnecting = false
            statusMessage = "Login failed: \(message ?? "Invalid username or password")"
            MixerClient.shared.disconnect()
        }
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
