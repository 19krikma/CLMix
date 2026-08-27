import Foundation

@MainActor
protocol MixerClientDelegate: AnyObject {
    func mixerDidConnect()
    func mixerDidDisconnect()
    func mixerDidFail(message: String)
    /// `token` is set whenever `ok` is true (both a fresh username/password
    /// login and a resumed one) - see SessionStore for what callers should
    /// do with it. Nil when `ok` is false.
    func mixerDidReceiveLoginResult(ok: Bool, message: String?, token: String?)
    func mixerDidReceiveAuxes(_ auxes: [AuxBus])
    func mixerDidReceiveBanks(_ banks: [String])
    func mixerDidReceiveLevels(aux: Int, channels: [ChannelState])
    func mixerDidReceivePresets(_ names: [String])
    func mixerDidSavePreset(_ name: String)
    func mixerDidLoadPreset(_ name: String)
}

/// Talks to the CLMix desktop app's RemoteServer
/// (services/remote_server.py) over a WebSocket, using the same JSON
/// protocol the Android app's MixerClient.kt speaks: login/list_auxes/
/// list_banks/select_aux/select_bank/set_level/set_pan/
/// list_presets/save_preset/load_preset out, login_result/auxes/banks/
/// levels/presets/preset_saved/preset_loaded/error in.
///
/// The server rejects every action until a successful "login" - callers
/// must send credentials via login() and wait for a true
/// mixerDidReceiveLoginResult before calling requestAuxes() or anything
/// else.
final class MixerClient: NSObject {
    static let shared = MixerClient()

    weak var delegate: MixerClientDelegate?
    private(set) var isConnected = false

    // Set from login_result - gates whether MixerView shows the Presets
    // UI at all, mirroring the server's own per-user permission check
    // (which still applies regardless of what the client shows).
    private(set) var presetsAllowed = false

    private var task: URLSessionWebSocketTask?
    private lazy var session = URLSession(
        configuration: .default, delegate: self, delegateQueue: nil
    )

    // Cancelling a task completes its in-flight receive() with an error,
    // which is indistinguishable from the socket genuinely dropping. Set
    // across a deliberate close so listen() can tell the two apart and
    // stay quiet - otherwise every logout/reconnect surfaces itself as a
    // spurious "Error: cancelled" and bounces the user to the login
    // screen they were already heading to.
    private var isClosing = false

    private override init() {}

    func connect(host: String, port: Int) {
        disconnect()

        guard let url = URL(string: "ws://\(host):\(port)") else {
            Task { @MainActor in self.delegate?.mixerDidFail(message: "Invalid address") }
            return
        }

        isClosing = false
        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()
        listen()
    }

    func disconnect() {
        isClosing = true
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        isConnected = false
        presetsAllowed = false
    }

    func login(username: String, password: String) {
        send(["action": "login", "username": username, "password": password])
    }

    /// Resumes a session saved by SessionStore instead of asking for
    /// credentials again. The server answers with the same login_result
    /// shape either way (RemoteServer._handle_token_login) - including a
    /// false result with "Session expired" if the token has aged out or
    /// the desktop app restarted since it was issued.
    func login(token: String) {
        send(["action": "login", "token": token])
    }

    /// Explicit logout: revokes the token server-side (RemoteServer pops
    /// it from _sessions) before the socket closes, so a copy of the
    /// token that outlived this app can't be redeemed afterwards.
    ///
    /// Closes only once the logout frame has actually been written -
    /// cancelling the task straight after queueing it would drop the
    /// frame, leaving the token live server-side until it aged out on its
    /// own, which is exactly what an explicit logout is meant to prevent.
    func logout(token: String?) {
        guard let token else {
            disconnect()
            return
        }

        send(["action": "logout", "token": token]) { [weak self] in
            self?.disconnect()
        }
    }

    func requestAuxes() {
        send(["action": "list_auxes"])
    }

    func requestBanks() {
        send(["action": "list_banks"])
    }

    func selectAux(_ aux: Int) {
        send(["action": "select_aux", "aux": aux])
    }

    func selectBank(_ bank: String?) {
        send(["action": "select_bank", "bank": bank ?? NSNull()])
    }

    func setLevel(channel: Int, db: Double) {
        send(["action": "set_level", "channel": channel, "level": db])
    }

    func setPan(channel: Int, pan: Double) {
        send(["action": "set_pan", "channel": channel, "pan": pan])
    }

    func requestPresets() {
        send(["action": "list_presets"])
    }

    func savePreset(name: String) {
        send(["action": "save_preset", "name": name])
    }

    func loadPreset(name: String) {
        send(["action": "load_preset", "name": name])
    }

    /// `completion` runs once the frame has been written (or immediately
    /// if there's nothing to write it to), on URLSession's queue rather
    /// than the main actor.
    private func send(_ payload: [String: Any], completion: (() -> Void)? = nil) {
        guard let task,
              let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else {
            completion?()
            return
        }

        task.send(.string(text)) { _ in completion?() }
    }

    private func listen() {
        task?.receive { [weak self] result in
            guard let self else { return }

            switch result {
            case .success(let message):
                if case .string(let text) = message {
                    self.handle(text)
                }
                self.listen()

            case .failure(let error):
                self.isConnected = false

                // A deliberate close isn't a failure worth reporting -
                // whoever called disconnect() has already driven the UI
                // wherever it needs to go.
                guard !self.isClosing else { return }

                Task { @MainActor in
                    self.delegate?.mixerDidFail(message: error.localizedDescription)
                }
            }
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            return
        }

        Task { @MainActor [self] in
            switch type {
            case "login_result":
                let ok = json["ok"] as? Bool ?? false
                presetsAllowed = ok && (json["presets"] as? Bool ?? false)
                let token = (json["token"] as? String).flatMap { $0.isEmpty ? nil : $0 }
                delegate?.mixerDidReceiveLoginResult(
                    ok: ok, message: json["message"] as? String, token: token
                )

            case "auxes":
                let entries = json["auxes"] as? [[String: Any]] ?? []
                let list = entries.compactMap { entry -> AuxBus? in
                    guard let index = entry["index"] as? Int,
                          let name = entry["name"] as? String else {
                        return nil
                    }
                    return AuxBus(index: index, name: name)
                }
                delegate?.mixerDidReceiveAuxes(list)

            case "banks":
                delegate?.mixerDidReceiveBanks(json["banks"] as? [String] ?? [])

            case "levels":
                guard let aux = json["aux"] as? Int else { return }

                let entries = json["channels"] as? [[String: Any]] ?? []
                let channels = entries.compactMap { entry -> ChannelState? in
                    guard let channel = entry["channel"] as? Int,
                          let name = entry["name"] as? String else {
                        return nil
                    }
                    return ChannelState(
                        channel: channel,
                        name: name,
                        level: entry["level"] as? Double,
                        pan: entry["pan"] as? Double,
                        muted: entry["muted"] as? Bool ?? false
                    )
                }
                delegate?.mixerDidReceiveLevels(aux: aux, channels: channels)

            case "presets":
                delegate?.mixerDidReceivePresets(json["presets"] as? [String] ?? [])

            case "preset_saved":
                if let name = json["name"] as? String {
                    delegate?.mixerDidSavePreset(name)
                }

            case "preset_loaded":
                if let name = json["name"] as? String {
                    delegate?.mixerDidLoadPreset(name)
                }

            case "error":
                delegate?.mixerDidFail(message: json["message"] as? String ?? "Unknown error")

            default:
                break
            }
        }
    }
}

extension MixerClient: URLSessionWebSocketDelegate {
    func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        isConnected = true
        Task { @MainActor in self.delegate?.mixerDidConnect() }
    }

    func urlSession(
        _ session: URLSession, webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?
    ) {
        isConnected = false
        Task { @MainActor in self.delegate?.mixerDidDisconnect() }
    }
}
