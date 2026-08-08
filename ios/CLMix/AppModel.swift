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

    private var pendingUsername = ""
    private var pendingPassword = ""

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

    func setMute(channel: Int, muted: Bool) {
        MixerClient.shared.setMute(channel: channel, muted: muted)
    }

    func logout() {
        MixerClient.shared.disconnect()
        channels = []
        auxes = []
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
}
