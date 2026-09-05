import Foundation

/// A stand-in for MixerClient that answers entirely from memory - no
/// socket, no desktop server, no console, no network of any kind.
///
/// This exists because the app cannot otherwise be reviewed. App Review has
/// no Windows machine running the CLMix desktop app and no DiGiCo Q225 to
/// point it at, so a reviewer reaching the login screen can get no further,
/// and Apple does not accept a video in place of a working build
/// (Guideline 2.1 - the first submission was rejected on exactly this).
///
/// It deliberately implements MixerBackend rather than being special-cased
/// inside AppModel, and it pushes levels on the same ~150ms cadence the
/// real RemoteServer does (services/remote_server.py), so every screen runs
/// its ordinary code path: LevelFaderView's drag grace and AppModel's
/// optimistic mute handling are both driven by those pushes arriving, and
/// would behave differently against a backend that stayed silent.
final class DemoMixer: MixerBackend {
    static let shared = DemoMixer()

    weak var delegate: MixerClientDelegate?
    private(set) var isConnected = false
    // The demo account is allowed everything, so the Presets UI is exercised
    // rather than hidden - "all of the features and functionality" is what
    // Guideline 2.1 asks a demonstration mode to show.
    private(set) var presetsAllowed = false

    private init() {}

    // MARK: - The fake console

    private struct DemoChannel {
        let channel: Int
        let name: String
        let bank: String
    }

    /// A plausible small-band input list, in console order. `bank` mirrors
    /// how the desktop app groups channels into banks the phone can filter
    /// by; "All" is not a bank here, it is the absence of a filter.
    private static let catalog: [DemoChannel] = [
        DemoChannel(channel: 1, name: "Kick In", bank: "Drums"),
        DemoChannel(channel: 2, name: "Kick Out", bank: "Drums"),
        DemoChannel(channel: 3, name: "Snare Top", bank: "Drums"),
        DemoChannel(channel: 4, name: "Snare Bot", bank: "Drums"),
        DemoChannel(channel: 5, name: "Hi-Hat", bank: "Drums"),
        DemoChannel(channel: 6, name: "Rack Tom", bank: "Drums"),
        DemoChannel(channel: 7, name: "Floor Tom", bank: "Drums"),
        DemoChannel(channel: 8, name: "OH L", bank: "Drums"),
        DemoChannel(channel: 9, name: "OH R", bank: "Drums"),
        DemoChannel(channel: 10, name: "Bass DI", bank: "Band"),
        DemoChannel(channel: 11, name: "Bass Amp", bank: "Band"),
        DemoChannel(channel: 12, name: "Gtr Stage L", bank: "Band"),
        DemoChannel(channel: 13, name: "Gtr Stage R", bank: "Band"),
        DemoChannel(channel: 14, name: "Keys L", bank: "Band"),
        DemoChannel(channel: 15, name: "Keys R", bank: "Band"),
        DemoChannel(channel: 16, name: "Acoustic", bank: "Band"),
        DemoChannel(channel: 17, name: "Lead Vox", bank: "Vocals"),
        DemoChannel(channel: 18, name: "BV Stage L", bank: "Vocals"),
        DemoChannel(channel: 19, name: "BV Stage R", bank: "Vocals"),
        DemoChannel(channel: 20, name: "Talkback", bank: "Vocals"),
    ]

    private static let auxes: [AuxBus] = [
        AuxBus(index: 1, name: "Wedge 1 - Lead Vox"),
        AuxBus(index: 2, name: "Wedge 2 - Guitar"),
        AuxBus(index: 3, name: "Wedge 3 - Bass"),
        AuxBus(index: 4, name: "Wedge 4 - Keys"),
        AuxBus(index: 5, name: "IEM - Drums"),
        AuxBus(index: 6, name: "IEM - MD"),
        AuxBus(index: 7, name: "Side Fills"),
        AuxBus(index: 8, name: "Drum Sub"),
    ]

    private static let banks = ["Drums", "Band", "Vocals"]

    private struct Send {
        var level: Double
        var pan: Double
        var muted: Bool
    }

    // [aux index: [channel number: send]]
    private var sends: [Int: [Int: Send]] = [:]
    private var presets: [String: [Int: Send]] = [:]

    private var selectedAux: Int?
    private var selectedBank: String?
    private var pushTimer: Timer?

    /// Deterministic starting positions, so the demo opens on something
    /// that reads as a real monitor mix rather than a wall of identical
    /// faders - each performer's own instrument sits loudest in their own
    /// wedge, and the talkback starts muted the way it usually would.
    private func seed() {
        sends = [:]

        for aux in Self.auxes {
            var mix: [Int: Send] = [:]

            for entry in Self.catalog {
                // Spread the rest of the band across a believable range,
                // varying per aux so switching aux visibly changes the mix.
                let spread = Double((entry.channel * 7 + aux.index * 13) % 24)
                var level = -6.0 - spread
                let isOwnInstrument = (entry.channel % Self.auxes.count) == (aux.index % Self.auxes.count)

                if isOwnInstrument { level = -2.0 }
                if entry.name == "Lead Vox" { level = -4.0 }

                // Pan follows the stage picture: anything named L/R sits
                // off-center, everything else stays up the middle.
                var pan = 0.0
                if entry.name.hasSuffix(" L") { pan = -0.4 }
                if entry.name.hasSuffix(" R") { pan = 0.4 }

                mix[entry.channel] = Send(
                    level: level, pan: pan, muted: entry.name == "Talkback"
                )
            }

            sends[aux.index] = mix
        }

        // Two presets to load straight away, so the Load sheet is not empty
        // the first time it is opened.
        presets = [
            "Opening Set": sends[1] ?? [:],
            "Acoustic Set": sends[4] ?? [:],
        ]
    }

    // MARK: - MixerBackend

    func connect(host: String, port: Int) {
        seed()
        selectedAux = nil
        selectedBank = nil
        isConnected = true

        Task { @MainActor in self.delegate?.mixerDidConnect() }
    }

    func disconnect() {
        stopPushing()
        isConnected = false
        presetsAllowed = false
        selectedAux = nil
        selectedBank = nil
    }

    func login(username: String, password: String) {
        // Any credentials are accepted: the demo exists to be walked into,
        // and there is no account behind it to get wrong.
        presetsAllowed = true

        // No token is handed back, so SessionStore stores nothing and a
        // relaunch returns to the connect screen rather than silently
        // resuming into a demo the user may not have wanted again.
        Task { @MainActor in
            self.delegate?.mixerDidReceiveLoginResult(ok: true, message: nil, token: nil)
        }
    }

    func login(token: String) {
        // Never reached: enterDemoMode always logs in with credentials, and
        // no demo token is ever persisted to resume from.
        login(username: "demo", password: "demo")
    }

    func logout(token: String?) {
        disconnect()
    }

    func requestAuxes() {
        Task { @MainActor in self.delegate?.mixerDidReceiveAuxes(Self.auxes) }
    }

    func requestBanks() {
        Task { @MainActor in self.delegate?.mixerDidReceiveBanks(Self.banks) }
    }

    func selectAux(_ aux: Int) {
        selectedAux = aux
        selectedBank = nil
        startPushing()
    }

    func selectBank(_ bank: String?) {
        selectedBank = bank
        pushLevels()
    }

    func setLevel(channel: Int, db: Double) {
        guard let aux = selectedAux else { return }
        sends[aux]?[channel]?.level = db
    }

    func setPan(channel: Int, pan: Double) {
        guard let aux = selectedAux else { return }
        sends[aux]?[channel]?.pan = pan
    }

    func setMute(channel: Int, muted: Bool) {
        guard let aux = selectedAux else { return }
        sends[aux]?[channel]?.muted = muted
    }

    func requestPresets() {
        let names = presets.keys.sorted()
        Task { @MainActor in self.delegate?.mixerDidReceivePresets(names) }
    }

    func savePreset(name: String) {
        guard let aux = selectedAux, let mix = sends[aux] else { return }
        presets[name] = mix

        Task { @MainActor in self.delegate?.mixerDidSavePreset(name) }
    }

    func loadPreset(name: String) {
        guard let aux = selectedAux, let stored = presets[name] else { return }

        // Applied as ordinary level/pan changes, exactly as the desktop app
        // recalls a preset - the next push reflects it like any other move.
        for (channel, send) in stored {
            sends[aux]?[channel]?.level = send.level
            sends[aux]?[channel]?.pan = send.pan
        }

        pushLevels()
        Task { @MainActor in self.delegate?.mixerDidLoadPreset(name) }
    }

    // MARK: - Pushing levels

    // Matches RemoteServer's own push cadence. Scheduled on the main run
    // loop, which is where every caller of this class already is.
    private func startPushing() {
        stopPushing()
        pushLevels()

        pushTimer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
            self?.pushLevels()
        }
    }

    private func stopPushing() {
        pushTimer?.invalidate()
        pushTimer = nil
    }

    private func pushLevels() {
        guard let aux = selectedAux, let mix = sends[aux] else { return }

        let visible = Self.catalog.filter { selectedBank == nil || $0.bank == selectedBank }
        let channels = visible.compactMap { entry -> ChannelState? in
            guard let send = mix[entry.channel] else { return nil }
            return ChannelState(
                channel: entry.channel,
                name: entry.name,
                level: send.level,
                pan: send.pan,
                muted: send.muted
            )
        }

        Task { @MainActor in
            self.delegate?.mixerDidReceiveLevels(aux: aux, channels: channels)
        }
    }
}
