import Foundation

@MainActor
protocol MixerClientDelegate: AnyObject {
    func mixerDidConnect()
    func mixerDidDisconnect()
    func mixerDidFail(message: String)
    func mixerDidReceiveLoginResult(ok: Bool, message: String?)
    func mixerDidReceiveAuxes(_ auxes: [AuxBus])
    func mixerDidReceiveBanks(_ banks: [String])
    func mixerDidReceiveLevels(aux: Int, channels: [ChannelState])
}

/// Talks to the CLMix desktop app's RemoteServer
/// (services/remote_server.py) over a WebSocket, using the same JSON
/// protocol the Android app's MixerClient.kt speaks: login/list_auxes/
/// list_banks/select_aux/select_bank/set_level/set_pan/set_mute out,
/// login_result/auxes/banks/levels/error in.
///
/// The server rejects every action until a successful "login" - callers
/// must send credentials via login() and wait for a true
/// mixerDidReceiveLoginResult before calling requestAuxes() or anything
/// else.
final class MixerClient: NSObject {
    static let shared = MixerClient()

    weak var delegate: MixerClientDelegate?
    private(set) var isConnected = false

    private var task: URLSessionWebSocketTask?
    private lazy var session = URLSession(
        configuration: .default, delegate: self, delegateQueue: nil
    )

    private override init() {}

    func connect(host: String, port: Int) {
        disconnect()

        guard let url = URL(string: "ws://\(host):\(port)") else {
            Task { @MainActor in self.delegate?.mixerDidFail(message: "Invalid address") }
            return
        }

        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()
        listen()
    }

    func disconnect() {
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        isConnected = false
    }

    func login(username: String, password: String) {
        send(["action": "login", "username": username, "password": password])
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

    func setMute(channel: Int, muted: Bool) {
        send(["action": "set_mute", "channel": channel, "muted": muted])
    }

    private func send(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else {
            return
        }

        task?.send(.string(text)) { _ in }
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
                delegate?.mixerDidReceiveLoginResult(ok: ok, message: json["message"] as? String)

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
