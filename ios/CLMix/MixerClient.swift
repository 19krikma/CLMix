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
    /// Arrives ~20x a second while a mix is moving, carrying one entry
    /// per visible channel. `sequence` advances per frame so views can
    /// tell a fresh sample from a redraw of the same one - the meter
    /// ballistics depend on that distinction (see ChannelMeterView).
    func mixerDidReceiveMeters(sequence: Int64, meters: [Int: MeterLevels])
    func mixerDidReceiveLevels(aux: Int, channels: [ChannelState])
    func mixerDidReceivePresets(_ names: [String])
    func mixerDidSavePreset(_ name: String)
    func mixerDidLoadPreset(_ name: String)
}

/// What AppModel drives, so a demo session (DemoMixer) can stand in for a
/// real one without AppModel branching on which it holds. Every member here
/// already existed on MixerClient - this only names the surface.
///
/// REMOVE WITH DEMO MODE: once the app is published and DemoMixer.swift is
/// deleted, this protocol has one implementer left and can be folded back
/// into MixerClient, with AppModel's `backend` going back to
/// `MixerClient.shared` directly. See ios/README.md's "Demo mode" section.
protocol MixerBackend: AnyObject {
    var delegate: MixerClientDelegate? { get set }
    var isConnected: Bool { get }
    var presetsAllowed: Bool { get }
    var muteAllowed: Bool { get }
    var mixerControlAllowed: Bool { get }
    var gainRange: ClosedRange<Double> { get }
    var trimRange: ClosedRange<Double> { get }

    func connect(host: String, port: Int)
    func disconnect()
    func login(username: String, password: String)
    func login(token: String)
    func logout(token: String?)
    func requestAuxes()
    func requestBanks()
    func selectAux(_ aux: Int)
    func selectMixerControl()
    func selectBank(_ bank: String?)
    func setLevel(channel: Int, db: Double)
    func setPan(channel: Int, pan: Double)
    func setMute(channel: Int, muted: Bool, hard: Bool)
    func setGain(channel: Int, gain: Double)
    func setTrim(channel: Int, trim: Double)
    func setPhantom(channel: Int, phantom: Bool)
    func setPhase(channel: Int, phase: Int)
    func setName(channel: Int, name: String)
    func requestPresets()
    func savePreset(name: String)
    func loadPreset(name: String)
}

/// Talks to the CLMix desktop app's RemoteServer
/// (services/remote_server.py) over a WebSocket, using the same JSON
/// protocol the Android app's MixerClient.kt speaks: login/logout/
/// list_auxes/list_banks/select_aux/select_mixer/select_bank/set_level/
/// set_pan/set_mute/set_gain/set_trim/set_phantom/set_phase/set_name/
/// list_presets/save_preset/load_preset out, login_result/auxes/banks/
/// levels/meters/presets/preset_saved/preset_loaded/error in.
///
/// The server rejects every action until a successful "login" - callers
/// must send credentials via login() and wait for a true
/// mixerDidReceiveLoginResult before calling requestAuxes() or anything
/// else.
final class MixerClient: NSObject, MixerBackend {
    static let shared = MixerClient()

    weak var delegate: MixerClientDelegate?
    private(set) var isConnected = false

    // Set from login_result - gates whether MixerView shows the Presets
    // UI at all, mirroring the server's own per-user permission check
    // (which still applies regardless of what the client shows).
    private(set) var presetsAllowed = false

    // Set from login_result - gates whether the Mute button is offered,
    // mirroring the server's own per-user check (which still applies
    // regardless of what the client shows). Defaults true: an account
    // with no explicit setting, and an older server that never sends the
    // field, both mean "allowed".
    private(set) var muteAllowed = true

    // Set from login_result - whether this account may take the console's
    // own channel faders, mutes and pans (the main mix) rather than one
    // performer's sends. Gates the choice offered right after login; the
    // server checks it again on every write regardless. Defaults false,
    // which is also what an older server that never sends the field
    // means.
    private(set) var mixerControlAllowed = false

    // Set from login_result - the range each head-amp dial sweeps, in dB,
    // as the desktop reads it off the console's own parameter table. Held
    // here rather than baked into the sheet so a corrected range ships with
    // the desktop instead of waiting on an App Store release; the defaults
    // are the same values and are what an older server that never sends the
    // field leaves in place.
    private(set) var gainRange = HeadAmpRange.gain
    private(set) var trimRange = HeadAmpRange.trim

    // Advances once per received meter frame. The server only sends a
    // frame when something actually changed, so a bar that stops being
    // fed stops being pushed back up and releases away, exactly as on
    // the desk.
    private var meterSequence: Int64 = 0

    // The one protocol error AppModel treats specially rather than just
    // displaying: the account is scoped to a different snapshot than the
    // one currently live on the console, which is a standing permissions
    // problem rather than anything retrying will fix. Mirrors Android's
    // MixerClient.SNAPSHOT_DENIED.
    static let snapshotDenied = "Access denied: not permitted for the current snapshot"

    // What the "aux" field of a levels frame carries in mixer mode: no
    // bus is being ridden, and the server sends a value no real aux index
    // could collide with rather than dropping the field the aux screens
    // already parse.
    static let mixerAux = -1

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
        muteAllowed = true
        mixerControlAllowed = false
        gainRange = HeadAmpRange.gain
        trimRange = HeadAmpRange.trim
    }

    /// Folds in the head-amp ranges from login_result, if the server sent
    /// any. A malformed or inverted pair is ignored rather than applied - a
    /// dial whose lower bound exceeds its upper cannot be turned at all,
    /// and the built-in defaults are correct for every console seen so far.
    private func readHeadAmpRanges(_ headAmp: [String: Any]?) {
        gainRange = Self.range(headAmp, "gain") ?? HeadAmpRange.gain
        trimRange = Self.range(headAmp, "trim") ?? HeadAmpRange.trim
    }

    private static func range(
        _ headAmp: [String: Any]?, _ key: String
    ) -> ClosedRange<Double>? {
        guard let pair = headAmp?[key] as? [Double], pair.count >= 2,
              pair[0] < pair[1] else {
            return nil
        }
        return pair[0]...pair[1]
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

    /// Switches this socket to full mixer control: from here on the
    /// level/pan/mute actions below ride the console's own channel fader,
    /// panner and mute instead of an aux's sends. selectAux() switches it
    /// back. Refused unless the account holds the permission.
    func selectMixerControl() {
        send(["action": "select_mixer"])
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

    /// In aux mode this mutes the channel in the selected mix only (the
    /// server writes the console's per-send on/off flag). In mixer mode it
    /// writes the console's own channel mute - and with `hard` set, also
    /// drops every aux send, taking the channel out of the monitors as
    /// well as the room. The console has no single control for that on an
    /// input channel; the server assembles it.
    func setMute(channel: Int, muted: Bool, hard: Bool = false) {
        send(["action": "set_mute", "channel": channel, "muted": muted, "hard": hard])
    }

    /// The channel's own input stage. Mixer mode only: a head amp feeds
    /// every mix and the recording at once, so the server refuses these
    /// outright on an aux socket.
    func setGain(channel: Int, gain: Double) {
        send(["action": "set_gain", "channel": channel, "gain": gain])
    }

    func setTrim(channel: Int, trim: Double) {
        send(["action": "set_trim", "channel": channel, "trim": trim])
    }

    func setPhantom(channel: Int, phantom: Bool) {
        send(["action": "set_phantom", "channel": channel, "phantom": phantom])
    }

    /// `phase` is the console's own enum, not a flag - 0 normal, 1...3 the
    /// inverted states a stereo channel has (see ChannelState.phase). The
    /// number is sent so a state the desk already holds is written back
    /// unchanged rather than collapsed to 1.
    func setPhase(channel: Int, phase: Int) {
        send(["action": "set_phase", "channel": channel, "phase": phase])
    }

    /// Renames the channel on the console itself - every surface and every
    /// other phone sees it. The server trims and caps the text.
    func setName(channel: Int, name: String) {
        send(["action": "set_name", "channel": channel, "name": name])
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
                muteAllowed = !ok || (json["mute"] as? Bool ?? true)
                mixerControlAllowed = ok && (json["mixer_control"] as? Bool ?? false)
                readHeadAmpRanges(json["head_amp"] as? [String: Any])
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
                    return AuxBus(
                        index: index, name: name,
                        stereo: entry["stereo"] as? Bool ?? true
                    )
                }
                delegate?.mixerDidReceiveAuxes(list)

            case "banks":
                delegate?.mixerDidReceiveBanks(json["banks"] as? [String] ?? [])

            case "levels":
                // Mixer-mode frames carry no bus - see mixerAux.
                let aux = json["aux"] as? Int ?? Self.mixerAux

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
                        muted: entry["muted"] as? Bool ?? false,
                        stereo: entry["stereo"] as? Bool ?? false,
                        gain: entry["gain"] as? Double,
                        trim: entry["trim"] as? Double,
                        phantom: entry["phantom"] as? Bool ?? false,
                        // phase_state is the console's value, 0...3. An
                        // older server sends only the "phase" bool, which
                        // cannot tell 3 from 1 - falling back to it loses
                        // which leg is inverted but still lights the
                        // button, which is what that server could do too.
                        phase: entry["phase_state"] as? Int
                            ?? ((entry["phase"] as? Bool ?? false)
                                ? PhaseState.inverted : PhaseState.normal)
                    )
                }
                delegate?.mixerDidReceiveLevels(aux: aux, channels: channels)

            case "meters":
                // Positional [channel, peakL, rmsL, peakR, rmsR] rows -
                // see RemoteServer._meter_states for why they are not
                // objects: this goes out 20 times a second, and field
                // names would be most of the payload.
                let rows = json["meters"] as? [[Any]] ?? []
                var frame = [Int: MeterLevels](minimumCapacity: rows.count)

                for row in rows {
                    guard let channel = row.first as? Int else { continue }

                    // Anything the console reported at its no-signal
                    // sentinel arrives as null, which fails the cast and
                    // lands as nil - exactly what MeterLevels wants.
                    let values = (1...4).map { index -> Double? in
                        index < row.count ? row[index] as? Double : nil
                    }

                    frame[channel] = MeterLevels(
                        leftPeak: values[0], leftRms: values[1],
                        rightPeak: values[2], rightRms: values[3]
                    )
                }

                meterSequence += 1
                delegate?.mixerDidReceiveMeters(sequence: meterSequence, meters: frame)

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
                let raw = json["message"] as? String ?? "Unknown error"
                delegate?.mixerDidFail(message: friendlyServerMessage(raw))

            default:
                break
            }
        }
    }
}

// Translates RemoteServer's raw protocol error strings
// (services/remote_server.py) into wording that reads as a plain
// user-facing message rather than a log line - permission rejections in
// particular get an explicit "Access denied" prefix so they can't be
// mistaken for a network problem. Anything not recognized here is
// already a plain sentence from the server, so it's passed through
// unchanged. Mirrors Android's MixerClient.kt friendlyServerMessage.
private func friendlyServerMessage(_ raw: String) -> String {
    switch raw {
    case "Not permitted for the current snapshot": return MixerClient.snapshotDenied
    case "Not permitted for this aux": return "Access denied: not permitted for this aux"
    case "Not permitted for presets": return "Access denied: not permitted for presets"
    case "Not permitted for mixer control":
        return "Access denied: not permitted for full mixer control"
    case "Mixer not connected": return "Mixer not connected - try again shortly"
    case "Not authenticated": return "Not logged in"
    default: return raw
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
