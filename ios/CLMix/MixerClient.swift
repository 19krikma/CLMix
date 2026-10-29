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
    /// This account's own banks, with the console's full channel list
    /// to pick from. Both arrive together because the editor needs both
    /// and neither is worth a round trip of its own.
    func mixerDidReceiveCustomBanks(banks: [CustomBank], channels: [BankChannel])
    func mixerDidReceivePresets(_ presets: [Preset])
    /// The accounts this one can share a preset with.
    /// `names` maps username to "First Last"; missing where unset.
    func mixerDidReceiveShareTargets(_ users: [String], names: [String: String])
    func mixerDidSavePreset(_ name: String)
    func mixerDidLoadPreset(_ name: String)
    /// The open EQ screen's channel, on the levels cadence, for as long
    /// as selectEq has one selected.
    func mixerDidReceiveEq(_ eq: ChannelEq)
    /// The open Dynamics screen's channel, on the levels cadence.
    func mixerDidReceiveDynamics(_ dynamics: ChannelDynamics)
    /// The desktop began (true) or finished (false) resyncing with the
    /// console. The server has dropped this phone's selection for the
    /// duration - see AppModel.mixerDidReceiveSync.
    func mixerDidReceiveSync(_ syncing: Bool)
    /// The server passed this phone's SOS on to the sound engineer.
    func mixerDidSendSos()
    /// The SOS did not go - `message` is the server's reason.
    func mixerDidFailSos(message: String)
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
    var personalizationAllowed: Bool { get }
    var liveSnapshot: String? { get }
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
    func setMute(channel: Int, muted: Bool)
    func selectEq(_ channel: Int?)
    func setEq(channel: Int, _ write: EqWrite)
    func selectDynamics(_ channel: Int?)
    func setDynamics(channel: Int, _ write: DynWrite)
    func setGain(channel: Int, gain: Double)
    func setTrim(channel: Int, trim: Double)
    func setPhantom(channel: Int, phantom: Bool)
    func setAltGain(channel: Int, gain: Double)
    func setAltPhantom(channel: Int, phantom: Bool)
    func setAltIn(channel: Int, altIn: Bool)
    func setPhase(channel: Int, phase: Int)
    func setName(channel: Int, name: String)
    func setPersonalName(channel: Int, name: String)
    func requestCustomBanks()
    func saveCustomBanks(_ banks: [CustomBank])
    func resetCustomBanks()
    /// An SOS to the sound engineer; `reason` is one of the server's
    /// SOS_REASONS keys. See SosButton.
    func sendSos(reason: String)
    func requestPresets()
    func savePreset(name: String)
    func loadPreset(_ preset: Preset)
    // The owner's alone - each is answered with a fresh presets list.
    func deletePreset(id: String)
    func setPresetPublic(id: String, isPublic: Bool)
    func sharePreset(id: String, users: [String])
    /// Named, so the server lists only the accounts that can use that
    /// preset's aux.
    func requestShareTargets(id: String)
}

/// Talks to the CLMix desktop app's RemoteServer
/// (services/remote_server.py) over a WebSocket, using the same JSON
/// protocol the Android app's MixerClient.kt speaks: login/logout/
/// list_auxes/list_banks/select_aux/select_mixer/select_bank/select_eq/
/// select_dyn/set_level/set_pan/set_mute/set_eq/set_dyn/set_gain/set_trim/set_phantom/
/// set_alt_gain/set_alt_phantom/set_alt_in/set_phase/set_name/
/// set_personal_name/
/// list_presets/save_preset/load_preset out, login_result/auxes/banks/
/// levels/eq/dynamics/meters/presets/preset_saved/preset_loaded/error in.
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

    // Set from login_result - whether this account may give channels its
    // own labels. Purely a display thing: the server files the label
    // against the account and swaps it into this connection's pushes, so
    // nothing about it reaches the console or any other account.
    // Defaults false, which is also what an older server that never
    // sends the field means.
    private(set) var personalizationAllowed = false

    // The snapshot live on the console right now, from the levels
    // frames - not the account's snapshot *scope*, which may be "All
    // Snapshots". A personal rename is filed against this, so the rename
    // dialog can name the show the label will belong to. Nil until the
    // first frame, or against a server too old to send it.
    private(set) var liveSnapshot: String?

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
        personalizationAllowed = false
        liveSnapshot = nil
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
        send(["action": "login", "username": username, "password": password,
              "features": Self.features])
    }

    /// What this app understands beyond the original protocol, sent with
    /// every login so the server can tell it from an older one. "sync":
    /// it handles the desktop's resync - see AppModel.mixerDidReceiveSync.
    private static let features = ["sync"]

    /// Resumes a session saved by SessionStore instead of asking for
    /// credentials again. The server answers with the same login_result
    /// shape either way (RemoteServer._handle_token_login) - including a
    /// false result with "Session expired" if the token has aged out or
    /// the desktop app restarted since it was issued.
    func login(token: String) {
        send(["action": "login", "token": token, "features": Self.features])
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
    /// writes the console's own channel mute.
    func setMute(channel: Int, muted: Bool) {
        send(["action": "set_mute", "channel": channel, "muted": muted])
    }

    /// Opens (or, with nil, closes) one channel's EQ: from here on the
    /// server follows each levels push with an "eq" frame for it. Mixer
    /// mode only, like everything else that reaches the channel itself.
    func selectEq(_ channel: Int?) {
        send(["action": "select_eq", "channel": channel.map { $0 as Any } ?? NSNull()])
    }

    /// Opens (or, with nil, closes) one channel's Dynamics screen - the
    /// same arrangement as selectEq, for a "dynamics" frame.
    func selectDynamics(_ channel: Int?) {
        send(["action": "select_dyn", "channel": channel.map { $0 as Any } ?? NSNull()])
    }

    func setDynamics(channel: Int, _ write: DynWrite) {
        var payload: [String: Any] = ["action": "set_dyn", "channel": channel]

        switch write {
        case .compIn(let isIn):
            payload["param"] = "comp_in"
            payload["value"] = isIn
        case .gateIn(let isIn):
            payload["param"] = "gate_in"
            payload["value"] = isIn
        case .bandIn(let band, let isIn):
            payload["param"] = "comp_band_in_\(band)"
            payload["value"] = isIn
        case .value(let param, let value):
            payload["param"] = param.wireName
            payload["value"] = value
        }

        send(payload)
    }

    func setEq(channel: Int, _ write: EqWrite) {
        var payload: [String: Any] = ["action": "set_eq", "channel": channel]

        switch write {
        case .eqIn(let isIn):
            payload["param"] = "eq_in"
            payload["value"] = isIn
        case .bandOn(let band, let on):
            payload["param"] = "band_on"
            payload["band"] = band
            payload["value"] = on
        case .freq(let band, let hz):
            payload["param"] = "freq"
            payload["band"] = band
            payload["value"] = hz
        case .gain(let band, let db):
            payload["param"] = "gain"
            payload["band"] = band
            payload["value"] = db
        case .q(let band, let q):
            payload["param"] = "q"
            payload["band"] = band
            payload["value"] = q
        case .filterIn(let lo, let isIn):
            payload["param"] = lo ? "lo_filter_in" : "hi_filter_in"
            payload["value"] = isIn
        case .filterFreq(let lo, let hz):
            payload["param"] = lo ? "lo_filter_freq" : "hi_filter_freq"
            payload["value"] = hz
        }

        send(payload)
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

    /// The alternate input's own head amp. Trim has no alt twin -
    /// setTrim covers whichever input is live.
    func setAltGain(channel: Int, gain: Double) {
        send(["action": "set_alt_gain", "channel": channel, "gain": gain])
    }

    func setAltPhantom(channel: Int, phantom: Bool) {
        send(["action": "set_alt_phantom", "channel": channel, "phantom": phantom])
    }

    /// Switches the channel between its main (false) and alt (true) input.
    func setAltIn(channel: Int, altIn: Bool) {
        send(["action": "set_alt_in", "channel": channel, "alt_in": altIn])
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

    /// Labels one channel for this account alone.
    ///
    /// The near-opposite of setName above: nothing here reaches the desk. The server
    /// stores the label against this account and the snapshot currently
    /// live, and swaps it into this connection's own pushes - no other
    /// phone, and no surface on the console, ever sees it. An empty name
    /// clears the label and puts the console's own name back.
    func setPersonalName(channel: Int, name: String) {
        send(["action": "set_personal_name", "channel": channel, "name": name])
    }

    /// This account's own banks and the channel list to build them
    /// from. Behind the personalization permission, like personal
    /// names and for the same reason: it is this account's view of a
    /// console everyone else is sharing, and nothing it writes reaches
    /// the desk.
    func requestCustomBanks() {
        send(["action": "list_custom_banks"])
    }

    /// Replaces the whole set. Whole set rather than one bank at a time
    /// because add, remove, rename and re-checking a bank's channels
    /// are then the same write, and the order sent is the order the
    /// picker shows.
    func saveCustomBanks(_ banks: [CustomBank]) {
        send([
            "action": "save_custom_banks",
            "banks": banks.map { ["name": $0.name, "channels": $0.channels] },
        ])
    }

    /// Throws this account's set away so the server seeds it from the
    /// console's own banks again.
    func resetCustomBanks() {
        send(["action": "reset_custom_banks"])
    }

    func sendSos(reason: String) {
        send(["action": "sos", "reason": reason])
    }

    func requestPresets() {
        // any_aux: every preset this account can see, not just this
        // aux's - the Load sheet asks before loading across auxes.
        send(["action": "list_presets", "any_aux": true])
    }

    func savePreset(name: String) {
        send(["action": "save_preset", "name": name])
    }

    // By id where the server gave one; by name on a server from before
    // owners, which never did.
    func loadPreset(_ preset: Preset) {
        if preset.serverID.isEmpty {
            send(["action": "load_preset", "name": preset.name, "any_aux": true])
        } else {
            // any_aux: already confirmed by the user when the aux differs.
            send(["action": "load_preset", "id": preset.serverID, "any_aux": true])
        }
    }

    func deletePreset(id: String) {
        send(["action": "delete_preset", "id": id])
    }

    func setPresetPublic(id: String, isPublic: Bool) {
        send(["action": "set_preset_public", "id": id, "public": isPublic])
    }

    func sharePreset(id: String, users: [String]) {
        send(["action": "share_preset", "id": id, "users": users])
    }

    func requestShareTargets(id: String) {
        send(["action": "list_share_targets", "id": id])
    }

    /// A server from before owners sends names alone, all of them open to
    /// everyone - which is what they become here.
    private static func parsePresets(_ json: [String: Any]) -> [Preset] {
        guard let items = json["items"] as? [[String: Any]] else {
            return (json["presets"] as? [String] ?? []).map {
                Preset(serverID: "", name: $0, owner: nil, mine: false,
                       isPublic: true, sharedWithMe: false, sharedWith: [])
            }
        }

        return items.compactMap { item in
            guard let id = item["id"] as? String,
                  let name = item["name"] as? String else { return nil }
            return Preset(
                serverID: id,
                name: name,
                owner: item["owner"] as? String,
                mine: item["mine"] as? Bool ?? false,
                isPublic: item["public"] as? Bool ?? false,
                sharedWithMe: item["shared_with_me"] as? Bool ?? false,
                sharedWith: item["shared_with"] as? [String] ?? [],
                aux: item["aux"] as? Int,
                auxName: item["aux_name"] as? String,
                ownerName: item["owner_name"] as? String,
                sharedWithNames: item["shared_with_names"] as? [String] ?? []
            )
        }
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
                personalizationAllowed =
                    ok && (json["personalization"] as? Bool ?? false)
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

            case "custom_banks":
                let banks = (json["banks"] as? [[String: Any]] ?? []).map { entry in
                    CustomBank(
                        name: entry["name"] as? String ?? "",
                        channels: entry["channels"] as? [Int] ?? []
                    )
                }

                let channels = (json["channels"] as? [[String: Any]] ?? []).compactMap {
                    entry -> BankChannel? in
                    guard let channel = entry["channel"] as? Int else { return nil }
                    return BankChannel(
                        channel: channel,
                        name: entry["name"] as? String ?? "Ch \(channel)"
                    )
                }

                delegate?.mixerDidReceiveCustomBanks(banks: banks, channels: channels)

            case "levels":
                // Mixer-mode frames carry no bus - see mixerAux.
                let aux = json["aux"] as? Int ?? Self.mixerAux

                liveSnapshot = (json["snapshot"] as? String)
                    .flatMap { $0.isEmpty ? nil : $0 }

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
                                ? PhaseState.inverted : PhaseState.normal),
                        altGain: entry["alt_gain"] as? Double,
                        altPhantom: entry["alt_phantom"] as? Bool ?? false,
                        altIn: entry["alt_in"] as? Bool ?? false,
                        altAvailable: entry["alt_available"] as? Bool ?? false,
                        eqIn: entry["eq_in"] as? Bool,
                        eq: (entry["eq_summary"] as? [Any]).flatMap {
                            Self.parseEqSummary(channel: channel, $0)
                        },
                        compIn: entry["comp_in"] as? Bool,
                        gateIn: entry["gate_in"] as? Bool,
                        dynamics: (entry["dyn_summary"] as? [Any]).flatMap {
                            Self.parseDynSummary(channel: channel, $0)
                        }
                    )
                }
                delegate?.mixerDidReceiveLevels(aux: aux, channels: channels)

            case "eq":
                delegate?.mixerDidReceiveEq(Self.parseEq(json))

            case "dynamics":
                delegate?.mixerDidReceiveDynamics(Self.parseDynamics(json))

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
                delegate?.mixerDidReceivePresets(Self.parsePresets(json))

            case "share_targets":
                delegate?.mixerDidReceiveShareTargets(
                    json["users"] as? [String] ?? [],
                    names: json["names"] as? [String: String] ?? [:]
                )

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
                // Only an SOS action draws errors that name it ("unknown
                // action 'sos'" from a desktop from before SOS).
                if raw.range(of: "sos", options: .caseInsensitive) != nil {
                    delegate?.mixerDidFailSos(message: raw)
                } else {
                    delegate?.mixerDidFail(message: friendlyServerMessage(raw))
                }

            case "sos_sent":
                delegate?.mixerDidSendSos()

            case "sync":
                delegate?.mixerDidReceiveSync(json["syncing"] as? Bool ?? false)

            default:
                break
            }
        }
    }
}

extension MixerClient {
    /// Always all four bands, in console order, whatever arrived - a band
    /// the server left out reads the same as one it has no values for.
    /// Shared with DemoMixer so the demo's frames go through the same
    /// reading a real server's do.
    /// A strip's EQ from the positional row RemoteServer._eq_summary
    /// sends: [eq_in, lo_in, lo_freq, hi_in, hi_freq, then on, freq, gain,
    /// q, curve per band]. Nil for a row too short to be one.
    static func parseEqSummary(channel: Int, _ row: [Any]) -> ChannelEq? {
        let head = 5, perBand = 5
        guard row.count >= head + EqRange.bandCount * perBand else { return nil }

        func flag(_ i: Int) -> Bool? { (row[i] as? Int).map { $0 != 0 } }
        func number(_ i: Int) -> Double? { row[i] as? Double }

        let bands = (1...EqRange.bandCount).map { band -> EqBand in
            let at = head + (band - 1) * perBand
            return EqBand(
                band: band, on: flag(at), freq: number(at + 1),
                gain: number(at + 2), q: number(at + 3), curve: row[at + 4] as? Int
            )
        }

        return ChannelEq(
            channel: channel,
            eqIn: flag(0),
            bands: bands,
            loFilter: EqFilter(isIn: flag(1), freq: number(2)),
            hiFilter: EqFilter(isIn: flag(3), freq: number(4))
        )
    }

    /// A strip's dynamics from RemoteServer._dyn_summary's positional row:
    /// [comp_in, comp_thresh, comp_ratio, comp_gain, gate_in, gate_thresh,
    /// gate_range, gate_mode, gate_ratio, gate_gain]. The last three came
    /// with desktop 1.22.0; an older server's seven-long row reads as a
    /// plain gate. Nil for a row too short to be one.
    static func parseDynSummary(channel: Int, _ row: [Any]) -> ChannelDynamics? {
        guard row.count >= 7 else { return nil }

        func flag(_ i: Int) -> Bool? { (row[i] as? Int).map { $0 != 0 } }
        func values(_ pairs: [(String, Int)]) -> [String: Double] {
            var result: [String: Double] = [:]
            for (key, i) in pairs where i < row.count {
                if let value = row[i] as? Double { result[key] = value }
            }
            return result
        }

        return ChannelDynamics(
            channel: channel,
            comp: DynSection(isIn: flag(0), values: values([("thresh", 1), ("ratio", 2), ("gain", 3)])),
            gate: DynSection(
                isIn: flag(4),
                values: values([("thresh", 5), ("range", 6), ("ratio", 8), ("gain", 9)]),
                mode: row.count > 7 ? (row[7] as? NSNumber)?.intValue : nil
            )
        )
    }

    static func parseDynamics(_ json: [String: Any]) -> ChannelDynamics {
        func section(_ key: String, _ params: [DynParam]) -> DynSection {
            let entry = json[key] as? [String: Any] ?? [:]
            var values: [String: Double] = [:]
            // Multiband's band switches ride along as 0/1 under band_in_n.
            for key in params.map(\.key) + (1...3).map({ "band_in_\($0)" }) {
                if let value = (entry[key] as? NSNumber)?.doubleValue { values[key] = value }
            }
            return DynSection(
                isIn: entry["in"] as? Bool, values: values,
                mode: entry["mode"] as? Int, knee: entry["knee"] as? Int
            )
        }

        return ChannelDynamics(
            channel: json["channel"] as? Int ?? 0,
            comp: section("comp", DynParam.comp),
            gate: section("gate", DynParam.gate)
        )
    }

    static func parseEq(_ json: [String: Any]) -> ChannelEq {
        let entries = json["bands"] as? [[String: Any]] ?? []
        var byNumber: [Int: [String: Any]] = [:]
        for entry in entries {
            if let band = entry["band"] as? Int { byNumber[band] = entry }
        }

        let bands = (1...EqRange.bandCount).map { number -> EqBand in
            guard let entry = byNumber[number] else { return EqBand(band: number) }
            return EqBand(
                band: number,
                on: entry["on"] as? Bool,
                freq: entry["freq"] as? Double,
                gain: entry["gain"] as? Double,
                q: entry["q"] as? Double,
                curve: entry["curve"] as? Int
            )
        }

        func filter(_ key: String) -> EqFilter {
            let entry = json[key] as? [String: Any] ?? [:]
            return EqFilter(isIn: entry["in"] as? Bool, freq: entry["freq"] as? Double)
        }

        return ChannelEq(
            channel: json["channel"] as? Int ?? 0,
            eqIn: json["eq_in"] as? Bool,
            bands: bands,
            loFilter: filter("lo_filter"),
            hiFilter: filter("hi_filter")
        )
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
    case "Not permitted for personalization":
        return "Access denied: not permitted to rename channels"
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
