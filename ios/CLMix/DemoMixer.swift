import Foundation

/// A stand-in for MixerClient that answers entirely from memory - no
/// socket, no desktop server, no console, no network of any kind.
///
/// This exists because the app cannot otherwise be reviewed. App Review has
/// no Windows machine running the CLMix desktop app and no mixing console to
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
    // Same default the real client uses - an account with no explicit
    // setting means "allowed".
    private(set) var muteAllowed = true
    private(set) var personalizationAllowed = false

    // The one snapshot this fake console knows about. The real server
    // files personal labels per snapshot because an account can span
    // several shows; there is only one show here, but the name is still
    // reported so the rename dialog reads exactly as it does for real.
    private(set) var liveSnapshot: String? = "Demo Show"

    // Personal labels, as the server's user record holds them - and like
    // that record, never written back into the catalog: the demo console
    // keeps its own names, and only this account's view of them changes.
    // Dropped on disconnect rather than persisted; the demo is meant to
    // be walked into fresh.
    private var personalNames: [Int: String] = [:]

    // The demo account holds Full Mixer Control too, so the choice after
    // login and the console's own faders behind it are both reachable -
    // "all of the features and functionality" is what Guideline 2.1 asks
    // a demonstration mode to show.
    private(set) var mixerControlAllowed = false

    // The demo console has the same head-amp ranges as a real one.
    let gainRange = HeadAmpRange.gain
    let trimRange = HeadAmpRange.trim

    private init() {}

    // MARK: - The fake console

    private struct DemoChannel {
        let channel: Int
        let name: String
        let bank: String
        // Whether the strip's meter draws two bars rather than one.
        var stereo: Bool = false
        // How this input behaves on a meter, so the demo's bars move like
        // a band playing rather than like noise. See meterAmplitude.
        var voice: Voice = .sustained
    }

    private enum Voice {
        case kick
        case snare
        case hats
        case toms
        case sustained
        case silent
    }

    /// A plausible small-band input list, in console order. `bank` mirrors
    /// how the desktop app groups channels into banks the phone can filter
    /// by; "All" is not a bank here, it is the absence of a filter.
    ///
    /// Two of them are stereo pairs, so the two-bar meter is exercised
    /// rather than only ever drawn as a single wide bar.
    private static let catalog: [DemoChannel] = [
        DemoChannel(channel: 1, name: "Kick In", bank: "Drums", voice: .kick),
        DemoChannel(channel: 2, name: "Kick Out", bank: "Drums", voice: .kick),
        DemoChannel(channel: 3, name: "Snare Top", bank: "Drums", voice: .snare),
        DemoChannel(channel: 4, name: "Snare Bot", bank: "Drums", voice: .snare),
        DemoChannel(channel: 5, name: "Hi-Hat", bank: "Drums", voice: .hats),
        DemoChannel(channel: 6, name: "Rack Tom", bank: "Drums", voice: .toms),
        DemoChannel(channel: 7, name: "Floor Tom", bank: "Drums", voice: .toms),
        DemoChannel(channel: 8, name: "OH L", bank: "Drums", voice: .hats),
        DemoChannel(channel: 9, name: "OH R", bank: "Drums", voice: .hats),
        DemoChannel(channel: 10, name: "Bass DI", bank: "Band"),
        DemoChannel(channel: 11, name: "Bass Amp", bank: "Band"),
        DemoChannel(channel: 12, name: "Gtr Stage L", bank: "Band"),
        DemoChannel(channel: 13, name: "Gtr Stage R", bank: "Band"),
        DemoChannel(channel: 14, name: "Keys", bank: "Band", stereo: true),
        DemoChannel(channel: 15, name: "Playback", bank: "Band", stereo: true),
        DemoChannel(channel: 16, name: "Acoustic", bank: "Band"),
        DemoChannel(channel: 17, name: "Lead Vox", bank: "Vocals"),
        DemoChannel(channel: 18, name: "BV Stage L", bank: "Vocals"),
        DemoChannel(channel: 19, name: "BV Stage R", bank: "Vocals"),
        DemoChannel(channel: 20, name: "Talkback", bank: "Vocals", voice: .silent),
    ]

    /// "Drum Sub" is deliberately mono - a real rig usually has one, and
    /// it is what makes the Pan button's disappearance on a bus with no
    /// pan axis visible in the demo rather than only against a console.
    private static let auxes: [AuxBus] = [
        AuxBus(index: 1, name: "Wedge 1 - Lead Vox"),
        AuxBus(index: 2, name: "Wedge 2 - Guitar"),
        AuxBus(index: 3, name: "Wedge 3 - Bass"),
        AuxBus(index: 4, name: "Wedge 4 - Keys"),
        AuxBus(index: 5, name: "IEM - Drums"),
        AuxBus(index: 6, name: "IEM - MD"),
        AuxBus(index: 7, name: "Side Fills"),
        AuxBus(index: 8, name: "Drum Sub", stereo: false),
    ]

    private static let banks = ["Drums", "Band", "Vocals"]

    /// What each wedge's owner most wants to hear, seeded loudest in their
    /// own mix - so the demo opens on something shaped like a real monitor
    /// mix instead of a wall of identical faders.
    private static let featuredChannel: [Int: Int] = [
        1: 17,  // Wedge 1 - Lead Vox -> Lead Vox
        2: 12,  // Wedge 2 - Guitar   -> Gtr Stage L
        3: 10,  // Wedge 3 - Bass     -> Bass DI
        4: 14,  // Wedge 4 - Keys     -> Keys
        5: 1,   // IEM - Drums        -> Kick In
        6: 16,  // IEM - MD           -> Acoustic
        7: 17,  // Side Fills         -> Lead Vox
        8: 3,   // Drum Sub           -> Snare Top
    ]

    private struct Send {
        var level: Double
        var pan: Double
        var muted: Bool
    }

    /// The console's own channel, as Full Mixer Control rides it - the
    /// channel fader and panner rather than one bus's send, plus the
    /// input stage behind them.
    private struct Strip {
        var name: String
        var level: Double
        var pan: Double?
        var muted: Bool
        var gain: Double?
        var trim: Double?
        var phantom: Bool
        // The console's own enum, 0..3 - see ChannelState.phase.
        var phase: Int
        // The alternate input slot. Nil gain means no alt route patched.
        var altGain: Double? = nil
        var altPhantom: Bool = false
        var altIn: Bool = false
        // The channel EQ, in console band order (1 = HF ... 4 = LF), and
        // the two filters ahead of it.
        var eq = ChannelEq(channel: 0)
        // The compressor and gate, as the server's dynamics frame has them.
        var dynamics = ChannelDynamics(channel: 0)
    }

    // [aux index: [channel number: send]]
    private var sends: [Int: [Int: Send]] = [:]
    // [id: preset]. A preset belongs to the aux it was saved from, as on
    // the real server, so the id is the aux and the name together - there
    // being only the one owner here.
    private var presets: [String: DemoPreset] = [:]

    private struct DemoPreset {
        let name: String
        let aux: Int
        let mix: [Int: Send]
        var isPublic = false
        var sharedWith: [String] = []
    }

    private static func presetID(aux: Int, name: String) -> String { "\(aux)/\(name)" }
    private static let demoOwner = "demo"
    private static let demoAccounts = ["Bass", "Drums", "Keys", "Vocals"]
    // [channel number: the console's own strip]
    private var console: [Int: Strip] = [:]

    private var selectedAux: Int?
    // True while the socket is riding the console's own faders. Mutually
    // exclusive with selectedAux, the same way the real server's two
    // modes are.
    private var mixerMode = false
    private var selectedBank: String?
    // The channel whose EQ screen is open, as the real server's
    // state["eq_channel"]. Mixer mode only.
    private var eqChannel: Int?
    private var dynChannel: Int?
    // This account's own banks, once it has any. Nil until seeded from
    // the catalog, exactly as the real server seeds from whatever the
    // console reports - see RemoteServer._stored_banks.
    private var customBanks: [CustomBank]?
    private var pushTimer: Timer?
    private var meterTimer: Timer?
    private var meterSequence: Int64 = 0

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

                // The vocal is up in everybody's mix, but whatever this
                // particular wedge is for sits above even that.
                if entry.name == "Lead Vox" { level = -4.0 }
                if entry.channel == Self.featuredChannel[aux.index] { level = -2.0 }

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

        // The console's own strips, behind every aux send above. Seeded
        // so the input stage has something to show the moment it is
        // opened: a head amp set where that source would really sit, a
        // trim near unity, and 48V only on what needs it. A couple of
        // channels deliberately report no head amp at all, which is what
        // a console that has not answered for them yet looks like - the
        // dials grey out and read a dash rather than 0 dB.
        console = [:]

        for entry in Self.catalog {
            let needsPhantom = entry.name.hasPrefix("OH") || entry.name.hasPrefix("BV")
                || entry.name == "Acoustic" || entry.name == "Talkback"
            let reported = entry.channel % 9 != 0

            console[entry.channel] = Strip(
                name: entry.name,
                level: Double((entry.channel * 5) % 11) * -1.2 - 1.0,
                // A mono channel has no pan axis on the main mix, exactly
                // as a mono aux has none for its sends.
                pan: entry.stereo ? 0 : panForStagePicture(entry.name),
                muted: entry.name == "Talkback",
                gain: reported ? Double(18 + (entry.channel * 7) % 34) : nil,
                trim: reported ? Double((entry.channel * 3) % 9) - 4 : nil,
                phantom: needsPhantom,
                // One inverted mono channel, and one stereo channel on a
                // state a boolean could not have carried - which is the
                // case the demo exists to show working.
                phase: phaseForStagePicture(entry.name),
                // A spare vocal mic on the vocal channels, so the Alt
                // section has something to show; the rest have none.
                altGain: entry.name == "Lead Vox" || entry.name.hasPrefix("BV")
                    ? Double(30 + entry.channel % 11) : nil,
                altPhantom: entry.name.hasPrefix("BV"),
                eq: seededEq(for: entry),
                dynamics: seededDynamics(for: entry)
            )
        }

        // Two presets to load straight away, so the Load sheet is not empty
        // the first time it is opened.
        presets = [
            Self.presetID(aux: 1, name: "Opening Set"):
                DemoPreset(name: "Opening Set", aux: 1, mix: sends[1] ?? [:]),
            Self.presetID(aux: 4, name: "Acoustic Set"):
                DemoPreset(name: "Acoustic Set", aux: 4, mix: sends[4] ?? [:]),
        ]
    }

    /// An EQ shaped like the source rather than four flat bands, so the
    /// graph opens on a curve worth looking at: a kick scooped in the
    /// boxy mids, a vocal with its rumble filtered off and some air on
    /// top. Everything else gets a gentle default the same shape as a
    /// desk's reset channel. The talkback has its EQ out, so EQ OUT is
    /// visible. Identical to Android's DemoMixer.seedEq.
    private func seededEq(for entry: DemoChannel) -> ChannelEq {
        func band(_ n: Int, _ freq: Double, _ gain: Double, _ q: Double,
                  curve: Int = 1, on: Bool = true) -> EqBand {
            EqBand(band: n, on: on, freq: freq, gain: gain, q: q, curve: curve)
        }

        let name = entry.name
        let vocal = name.contains("Vox") || name.hasPrefix("BV")
        let bands: [EqBand]

        if name.hasPrefix("Kick") {
            bands = [band(1, 4200, 5, 1.4), band(2, 1800, 0, 1.0),
                     band(3, 380, -8, 1.8), band(4, 62, 4, 1.2)]
        } else if name.hasPrefix("Snare") {
            bands = [band(1, 8000, 3, 0.8, curve: 2), band(2, 2500, 2, 1.5),
                     band(3, 900, -4, 2.5), band(4, 200, 3, 1.2)]
        } else if vocal {
            bands = [band(1, 12000, 3.5, 0.7, curve: 2), band(2, 3200, 1.5, 1.2),
                     band(3, 450, -3, 1.6), band(4, 120, 0, 1.0, on: false)]
        } else if name.hasPrefix("Bass") {
            bands = [band(1, 5000, 0, 0.7), band(2, 800, 2, 1.0),
                     band(3, 250, -3, 1.4), band(4, 70, 3, 1.0)]
        } else {
            bands = [band(1, 8000, 0, 0.71), band(2, 2000, 0, 0.71),
                     band(3, 500, 0, 0.71), band(4, 100, 0, 0.71)]
        }

        return ChannelEq(
            channel: entry.channel,
            eqIn: entry.voice != .silent,
            bands: bands,
            loFilter: EqFilter(
                isIn: vocal || name.hasPrefix("OH") || name == "Acoustic",
                freq: vocal ? 110 : 80
            ),
            hiFilter: EqFilter(
                isIn: name.hasPrefix("Kick"),
                freq: name.hasPrefix("Kick") ? 9000 : 18000
            )
        )
    }

    /// Compression on the vocals and bass, a gate on the drums - what a
    /// real input list would have in. Everything else carries the same
    /// settings, switched out. Identical to Android's seedDynamics.
    private func seededDynamics(for entry: DemoChannel) -> ChannelDynamics {
        let name = entry.name
        let vocal = name.contains("Vox") || name.hasPrefix("BV")
        let drum = [Voice.kick, .snare, .toms].contains(entry.voice)

        // One of each of Dynamics 1's other modes: the bass split three
        // ways, and a de-esser on a backing vocal - the desk's own defaults
        // for each (captured on channel 43, 2026-10-24).
        let mode = name == "Bass DI" ? 1 : name == "BV Stage L" ? 2 : 0
        var comp: [String: Double] = [
            "thresh": vocal ? -24 : -18, "ratio": vocal ? 3 : 4,
            "attack": 0.005, "release": 0.12, "gain": vocal ? 4 : 2,
            "thresh_2": -20, "ratio_2": 2, "attack_2": 0.01, "release_2": 0.2, "gain_2": 0,
            "thresh_3": -25, "ratio_3": 3, "attack_3": 0.005, "release_3": 0.1, "gain_3": 0,
            "all_thresh": -20, "all_gain": 0,
            "lp_xover": mode == 2 ? 5000 : 130, "hp_xover": mode == 2 ? 10000 : 1000,
            "desser_freq": 216, "desser_width": 25,
        ]
        for band in 1...3 {
            comp["band_in_\(band)"] = mode == 2 && band < 3 ? 0 : 1
        }

        // One of each of Dynamics 2's other modes, with the timings the
        // console switches to (captured on channel 22, 2026-10-25).
        let gateMode = name == "Playback" ? 1 : name == "Bass Amp" ? 2 : 0
        var gate: [String: Double] = [
            "thresh": drum ? -32 : -50, "range": 40, "attack": 0.0002,
            "hold": 0.05, "release": 0.15, "ratio": 3, "gain": 0,
        ]
        if gateMode == 1 {
            gate.merge(["thresh": -30, "range": 15, "attack": 0.005, "hold": 0.08, "release": 0.4]) { $1 }
        } else if gateMode == 2 {
            gate.merge(["thresh": -20, "attack": 0.01, "release": 0.5]) { $1 }
        }

        return ChannelDynamics(
            channel: entry.channel,
            comp: DynSection(
                isIn: vocal || name.hasPrefix("Bass"),
                values: comp,
                mode: mode, knee: 0
            ),
            gate: DynSection(isIn: drum || gateMode != 0, values: gate, mode: gateMode, knee: 0)
        )
    }

    /// Anything named L/R sits off-center, everything else stays up the
    /// middle - the same stage picture the aux sends are seeded with.
    private func panForStagePicture(_ name: String) -> Double {
        if name.hasSuffix(" L") { return -0.4 }
        if name.hasSuffix(" R") { return 0.4 }
        return 0
    }

    /// Polarity, as the console's own enum rather than a flag. "Snare Bot"
    /// is the mono channel an engineer would really flip. "Keys" is a
    /// stereo pair and is given state 2 so the demo carries at least one
    /// value a boolean could not have represented - tapping polarity off
    /// and on there has to come back to 2, which is the bug this enum
    /// exists to prevent.
    private func phaseForStagePicture(_ name: String) -> Int {
        switch name {
        case "Snare Bot": return PhaseState.inverted
        case "Keys": return 2
        default: return PhaseState.normal
        }
    }

    // MARK: - MixerBackend

    func connect(host: String, port: Int) {
        seed()
        selectedAux = nil
        eqChannel = nil
        dynChannel = nil
        mixerMode = false
        selectedBank = nil
        isConnected = true

        Task { @MainActor in self.delegate?.mixerDidConnect() }
    }

    func disconnect() {
        stopPushing()
        isConnected = false
        presetsAllowed = false
        muteAllowed = true
        personalizationAllowed = false
        personalNames = [:]
        customBanks = nil
        mixerControlAllowed = false
        selectedAux = nil
        eqChannel = nil
        dynChannel = nil
        mixerMode = false
        selectedBank = nil
    }

    func login(username: String, password: String) {
        // Any credentials are accepted: the demo exists to be walked into,
        // and there is no account behind it to get wrong.
        presetsAllowed = true
        muteAllowed = true
        personalizationAllowed = true
        mixerControlAllowed = true

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
        let names = bankNames()
        Task { @MainActor in self.delegate?.mixerDidReceiveBanks(names) }
    }

    /// Custom names in aux mode, the console's own in mixer mode - the
    /// same split the real server makes.
    private func bankNames() -> [String] {
        mixerMode ? Self.banks : storedBanks().map(\.name)
    }

    /// The account's own banks, seeded on first use from the grouping
    /// this fake console reports - the demo account holds
    /// personalization, so it has a set like any other such account.
    private func storedBanks() -> [CustomBank] {
        if let customBanks { return customBanks }

        let seeded = Self.banks.map { bank in
            CustomBank(
                name: bank,
                channels: Self.catalog.filter { $0.bank == bank }.map(\.channel)
            )
        }
        customBanks = seeded

        return seeded
    }

    func requestCustomBanks() {
        let banks = storedBanks()
        let channels = Self.catalog.map {
            BankChannel(channel: $0.channel, name: personalNames[$0.channel] ?? $0.name)
        }

        Task { @MainActor in
            self.delegate?.mixerDidReceiveCustomBanks(banks: banks, channels: channels)
        }
    }

    func saveCustomBanks(_ banks: [CustomBank]) {
        // The same cleaning the real server applies on the way in (see
        // UserStore._clean_banks), so the demo cannot accept a set the
        // desktop would have refused.
        customBanks = banks.compactMap { bank in
            let name = bank.name.trimmingCharacters(in: .whitespaces)

            guard !name.isEmpty else { return nil }

            var seen = Set<Int>()

            return CustomBank(
                name: String(name.prefix(32)),
                channels: bank.channels.filter { seen.insert($0).inserted }
            )
        }
        afterBanksChanged()
    }

    func resetCustomBanks() {
        customBanks = nil
        afterBanksChanged()
    }

    /// The set, the picker and the strips all follow a change to the
    /// banks - and a bank the client was sitting on may have just been
    /// renamed out from under it, in which case it falls back to the
    /// whole desk.
    private func afterBanksChanged() {
        requestCustomBanks()

        if let bank = selectedBank, !bankNames().contains(bank) {
            selectedBank = nil
        }

        let names = bankNames()
        Task { @MainActor in self.delegate?.mixerDidReceiveBanks(names) }
        pushLevels()
    }

    func selectAux(_ aux: Int) {
        selectedAux = aux
        eqChannel = nil
        dynChannel = nil
        mixerMode = false
        // The bank filter deliberately survives an aux change, matching
        // RemoteServer._handle (select_aux sets state["aux"] and leaves
        // state["bank"] alone). MixerView's own bank picker keeps its
        // selection across the switch too, so resetting here would leave the
        // picker reading "Drums" while every channel was actually showing.
        startPushing()
    }

    func selectMixerControl() {
        selectedAux = nil
        eqChannel = nil
        dynChannel = nil
        mixerMode = true
        startPushing()
    }

    func selectBank(_ bank: String?) {
        selectedBank = bank
        pushLevels()
    }

    func setLevel(channel: Int, db: Double) {
        if mixerMode {
            console[channel]?.level = db
            return
        }

        guard let aux = selectedAux else { return }
        sends[aux]?[channel]?.level = db
    }

    func setPan(channel: Int, pan: Double) {
        if mixerMode {
            // A channel the console reports no pan axis for stays
            // without one, the same as against real hardware.
            if console[channel]?.pan != nil { console[channel]?.pan = pan }
            return
        }

        guard let aux = selectedAux else { return }
        sends[aux]?[channel]?.pan = pan
    }

    func setMute(channel: Int, muted: Bool) {
        if mixerMode {
            console[channel]?.muted = muted
            return
        }

        guard let aux = selectedAux else { return }
        sends[aux]?[channel]?.muted = muted
    }

    // The account's own label for a strip - aux mode only, exactly as
    // the real server has it: mixer mode shows the desk's own names, and
    // setName below is what renames a channel there for real.
    func setPersonalName(channel: Int, name: String) {
        guard !mixerMode else { return }
        // An empty name clears the label, the same way the server reads
        // it, so Reset in the rename dialog works here too.
        personalNames[channel] = name.isEmpty ? nil : name
    }

    // The channel's input stage. Mixer mode only, exactly as the real
    // server has it - a head amp feeds every mix and the recording at
    // once, so it is never reachable from an aux socket.
    func setGain(channel: Int, gain: Double) {
        guard mixerMode else { return }
        console[channel]?.gain = gain
    }

    func setTrim(channel: Int, trim: Double) {
        guard mixerMode else { return }
        console[channel]?.trim = trim
    }

    func setPhantom(channel: Int, phantom: Bool) {
        guard mixerMode else { return }
        console[channel]?.phantom = phantom
    }

    func setAltGain(channel: Int, gain: Double) {
        guard mixerMode, console[channel]?.altGain != nil else { return }
        console[channel]?.altGain = gain
    }

    func setAltPhantom(channel: Int, phantom: Bool) {
        guard mixerMode, console[channel]?.altGain != nil else { return }
        console[channel]?.altPhantom = phantom
    }

    /// Refused onto an empty alt slot, as the real server does.
    func setAltIn(channel: Int, altIn: Bool) {
        guard mixerMode else { return }
        guard !altIn || console[channel]?.altGain != nil else { return }
        console[channel]?.altIn = altIn
    }

    func setPhase(channel: Int, phase: Int) {
        guard mixerMode else { return }
        console[channel]?.phase = phase
    }

    // The EQ screen. Mixer mode only, as the real server has it.
    func selectEq(_ channel: Int?) {
        guard mixerMode else { return }
        eqChannel = channel
        pushEq()
    }

    /// The real server's _set_eq, down to the clamping.
    func setEq(channel: Int, _ write: EqWrite) {
        guard mixerMode, var eq = console[channel]?.eq else { return }

        func clamp(_ value: Double, _ range: ClosedRange<Double>) -> Double {
            min(range.upperBound, max(range.lowerBound, value))
        }

        func band(_ n: Int, _ change: (inout EqBand) -> Void) {
            guard (1...eq.bands.count).contains(n) else { return }
            change(&eq.bands[n - 1])
        }

        switch write {
        case .eqIn(let isIn): eq.eqIn = isIn
        case .bandOn(let n, let on): band(n) { $0.on = on }
        case .freq(let n, let hz): band(n) { $0.freq = clamp(hz, EqRange.freq) }
        case .gain(let n, let db): band(n) { $0.gain = clamp(db, EqRange.gain) }
        case .q(let n, let q): band(n) { $0.q = clamp(q, EqRange.q) }
        case .filterIn(let lo, let isIn):
            if lo { eq.loFilter.isIn = isIn } else { eq.hiFilter.isIn = isIn }
        case .filterFreq(let lo, let hz):
            if lo {
                eq.loFilter.freq = clamp(hz, EqRange.freq)
            } else {
                eq.hiFilter.freq = clamp(hz, EqRange.freq)
            }
        }

        console[channel]?.eq = eq
    }

    private func pushEq() {
        guard let channel = eqChannel, let eq = console[channel]?.eq else { return }
        Task { @MainActor in self.delegate?.mixerDidReceiveEq(eq) }
    }

    func selectDynamics(_ channel: Int?) {
        guard mixerMode else { return }
        dynChannel = channel
        pushDynamics()
    }

    /// The real server's _set_dyn, down to the clamping.
    func setDynamics(channel: Int, _ write: DynWrite) {
        guard mixerMode, var dyn = console[channel]?.dynamics else { return }

        switch write {
        case .compIn(let isIn): dyn.comp.isIn = isIn
        case .gateIn(let isIn): dyn.gate.isIn = isIn
        case .bandIn(let band, let isIn): dyn.comp.values["band_in_\(band)"] = isIn ? 1 : 0
        case .value(let param, let value):
            let clamped = min(param.range.upperBound, max(param.range.lowerBound, value))
            if param.isComp {
                dyn.comp.values[param.key] = clamped
            } else {
                dyn.gate.values[param.key] = clamped
            }
        }

        console[channel]?.dynamics = dyn
    }

    private func pushDynamics() {
        guard let channel = dynChannel, let dyn = console[channel]?.dynamics else { return }
        Task { @MainActor in self.delegate?.mixerDidReceiveDynamics(dyn) }
    }

    func setName(channel: Int, name: String) {
        guard mixerMode else { return }
        // The same cap the real server applies (MAX_CHANNEL_NAME).
        console[channel]?.name = String(name.prefix(32))
    }

    /// Every aux's presets, this one's first, as the real server lists
    /// them for a phone that asks with any_aux.
    func requestPresets() {
        let list = presets
            .sorted {
                let here0 = $0.value.aux == selectedAux, here1 = $1.value.aux == selectedAux
                if here0 != here1 { return here0 }
                return $0.value.name.lowercased() < $1.value.name.lowercased()
            }
            .map { id, preset in
                Preset(serverID: id, name: preset.name, owner: Self.demoOwner, mine: true,
                       isPublic: preset.isPublic, sharedWithMe: false,
                       sharedWith: preset.sharedWith, aux: preset.aux,
                       auxName: Self.auxes.first { $0.index == preset.aux }?.name)
            }
        Task { @MainActor in self.delegate?.mixerDidReceivePresets(list) }
    }

    func deletePreset(id: String) {
        presets[id] = nil
        requestPresets()
    }

    func setPresetPublic(id: String, isPublic: Bool) {
        presets[id]?.isPublic = isPublic
        requestPresets()
    }

    func sharePreset(id: String, users: [String]) {
        presets[id]?.sharedWith = users.sorted()
        requestPresets()
    }

    func requestShareTargets(id: String) {
        let users = Self.demoAccounts
        Task { @MainActor in self.delegate?.mixerDidReceiveShareTargets(users, names: [:]) }
    }

    func savePreset(name: String) {
        guard let aux = selectedAux, let mix = sends[aux] else {
            // Never leave the sheet waiting on a reply that is not coming -
            // AppModel releases its pending completion on a failure.
            Task { @MainActor in self.delegate?.mixerDidFail(message: "No aux selected") }
            return
        }
        let id = Self.presetID(aux: aux, name: name)
        // Saving over a preset keeps who it is shared with.
        var preset = DemoPreset(name: name, aux: aux, mix: mix)
        preset.isPublic = presets[id]?.isPublic ?? false
        preset.sharedWith = presets[id]?.sharedWith ?? []
        presets[id] = preset

        Task { @MainActor in self.delegate?.mixerDidSavePreset(name) }
    }

    func loadPreset(_ preset: Preset) {
        let id = preset.serverID.isEmpty
            ? Self.presetID(aux: selectedAux ?? 0, name: preset.name)
            : preset.serverID
        // Any aux's: the Load sheet has already asked when it differs.
        guard let aux = selectedAux, let stored = presets[id] else {
            Task { @MainActor in self.delegate?.mixerDidFail(message: "Preset not found") }
            return
        }

        // Applied as ordinary level/pan changes, exactly as the desktop app
        // recalls a preset - the next push reflects it like any other move.
        for (channel, send) in stored.mix {
            sends[aux]?[channel]?.level = send.level
            sends[aux]?[channel]?.pan = send.pan
        }

        pushLevels()
        Task { @MainActor in self.delegate?.mixerDidLoadPreset(stored.name) }
    }

    // MARK: - Pushing levels

    // Matches RemoteServer's own push cadence. Scheduled on the main run
    // loop, which is where every caller of this class already is.
    private func startPushing() {
        stopPushing()
        pushLevels()

        // .common rather than scheduledTimer's default mode: the default
        // mode is suspended while the run loop tracks touches, which would
        // stall every push for the length of a fader drag or a sideways
        // scroll of the channel grid.
        let timer = Timer(timeInterval: 0.15, repeats: true) { [weak self] _ in
            self?.pushLevels()
        }
        RunLoop.main.add(timer, forMode: .common)
        pushTimer = timer

        // Meters get their own, faster loop, matching
        // METER_PUSH_INTERVAL_SECONDS in services/remote_server.py - at
        // the levels push's 150ms a meter reads as a row of steps rather
        // than a moving bar.
        let meters = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
            self?.pushMeters()
        }
        RunLoop.main.add(meters, forMode: .common)
        meterTimer = meters
    }

    private func stopPushing() {
        pushTimer?.invalidate()
        pushTimer = nil
        meterTimer?.invalidate()
        meterTimer = nil
    }

    private func pushLevels() {
        if mixerMode {
            pushMixerLevels()
            return
        }

        guard let aux = selectedAux, let mix = sends[aux] else { return }

        let visible = visibleCatalog()
        let channels = visible.compactMap { entry -> ChannelState? in
            guard let send = mix[entry.channel] else { return nil }
            return ChannelState(
                channel: entry.channel,
                // The account's own label wins over the console's name,
                // exactly as RemoteServer._channel_states applies it.
                name: personalNames[entry.channel] ?? entry.name,
                level: send.level,
                pan: send.pan,
                muted: send.muted,
                stereo: entry.stereo
            )
        }

        Task { @MainActor in
            self.delegate?.mixerDidReceiveLevels(aux: aux, channels: channels)
        }
    }

    /// The same row shape, read off the channel itself: the console's own
    /// fader, panner and mute, plus the input stage behind them. Carries
    /// the sentinel aux the real server sends in mixer mode, since no bus
    /// is being ridden.
    private func pushMixerLevels() {
        let visible = visibleCatalog()
        let channels = visible.compactMap { entry -> ChannelState? in
            guard let strip = console[entry.channel] else { return nil }
            return ChannelState(
                channel: entry.channel,
                name: strip.name,
                level: strip.level,
                pan: strip.pan,
                muted: strip.muted,
                stereo: entry.stereo,
                gain: strip.gain,
                trim: strip.trim,
                phantom: strip.phantom,
                phase: strip.phase,
                altGain: strip.altGain,
                altPhantom: strip.altPhantom,
                altIn: strip.altIn,
                altAvailable: strip.altGain != nil,
                eqIn: strip.eq.eqIn,
                eq: strip.eq,
                compIn: strip.dynamics.comp.isIn,
                gateIn: strip.dynamics.gate.isIn,
                dynamics: strip.dynamics
            )
        }

        Task { @MainActor in
            self.delegate?.mixerDidReceiveLevels(
                aux: MixerClient.mixerAux, channels: channels
            )
        }

        // Follows the levels frame, as the real push loop sends it.
        pushEq()
        pushDynamics()
    }

    /// Which strips this socket is looking at. A bank is resolved
    /// against the account's own set in aux mode and against the
    /// console's grouping in mixer mode, the same split the real server
    /// makes.
    private func visibleCatalog() -> [DemoChannel] {
        guard let bank = selectedBank else { return Self.catalog }

        if !mixerMode {
            guard let custom = storedBanks().first(where: { $0.name == bank }) else {
                return []
            }
            return Self.catalog.filter { custom.channels.contains($0.channel) }
        }

        return Self.catalog.filter { $0.bank == bank }
    }

    // MARK: - Fabricated meters

    /// Bars that move like a band playing rather than like noise: each
    /// input is given a voice (see Voice) driven off a shared 120bpm
    /// clock, so the kick pulses on the beat, the snare answers on 2 and
    /// 4, and the sustained sources breathe underneath. Entirely
    /// deterministic - a function of the wall clock and the channel
    /// number, with no state to keep between frames.
    private func pushMeters() {
        // Post-fader, so the bars follow whichever fader is being ridden:
        // the send in aux mode, the channel's own in mixer mode.
        let riding: (Int) -> (level: Double, muted: Bool)?

        if mixerMode {
            let strips = console
            riding = { channel in
                guard let strip = strips[channel] else { return nil }
                return (strip.level, strip.muted)
            }
        } else {
            guard let aux = selectedAux, let mix = sends[aux] else { return }
            riding = { channel in
                guard let send = mix[channel] else { return nil }
                return (send.level, send.muted)
            }
        }

        let now = Date.timeIntervalSinceReferenceDate
        let visible = visibleCatalog()
        var frame = [Int: MeterLevels](minimumCapacity: visible.count)

        for entry in visible {
            guard let state = riding(entry.channel) else { continue }

            let left = meterDb(entry, state, at: now, leg: 0)
            // A stereo pair's two legs never sit at exactly the same
            // level; a mono channel reports nothing at all on the right,
            // the same way the console's no-signal sentinel comes through
            // as null.
            let right = entry.stereo ? meterDb(entry, state, at: now, leg: 1) : nil

            frame[entry.channel] = MeterLevels(
                leftPeak: left,
                leftRms: left.map { $0 - 4 },
                rightPeak: right,
                rightRms: right.map { $0 - 4 }
            )
        }

        meterSequence += 1
        let sequence = meterSequence

        Task { @MainActor in
            self.delegate?.mixerDidReceiveMeters(sequence: sequence, meters: frame)
        }
    }

    private func meterDb(
        _ entry: DemoChannel, _ state: (level: Double, muted: Bool), at t: Double, leg: Int
    ) -> Double? {
        // A muted send is not in this mix, so its post-fader meter reads
        // nothing - the same as the console's no-signal sentinel.
        if state.muted { return nil }

        let offset = Double(entry.channel) * 0.37 + Double(leg) * 0.11
        let amplitude = meterAmplitude(entry.voice, at: t, offset: offset)

        if amplitude <= 0 { return nil }

        let base = Self.meterFloorDb + amplitude * (Self.meterCeilingDb - Self.meterFloorDb)

        // These are post-fader meters, so pulling a fader down has to pull
        // its bar down with it. The fader's contribution is clamped rather
        // than applied in full: the seeded mix sits well below unity, and
        // at face value every bar would start pinned to the floor with
        // nothing to show.
        let fader = max(-24, min(0, state.level + 6))
        let db = base + fader

        return db <= Self.meterFloorDb ? nil : db
    }

    private func meterAmplitude(_ voice: Voice, at t: Double, offset: Double) -> Double {
        let bar = (t / Self.beatSeconds).truncatingRemainder(dividingBy: 4)

        switch voice {
        case .kick:
            return percussive(bar, hits: [0, 2, 2.75], decay: 9)
        case .snare:
            return percussive(bar, hits: [1, 3], decay: 7)
        case .hats:
            return percussive(bar, hits: [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5], decay: 12) * 0.65
        case .toms:
            // Only around the turnaround, so they sit still most of the
            // bar the way a real fill does.
            return percussive(bar, hits: [3.5, 3.75], decay: 10) * 0.8
        case .sustained:
            let slow = sin(t * 0.7 + offset) * 0.5 + 0.5
            let fast = sin(t * 5.3 + offset * 2.1) * 0.5 + 0.5
            return min(1, 0.45 + 0.35 * slow + 0.15 * fast)
        case .silent:
            return 0
        }
    }

    /// A decaying hit at each of `hits`, wrapping around the bar so the
    /// last one still rings into the first beat of the next.
    private func percussive(_ bar: Double, hits: [Double], decay: Double) -> Double {
        var loudest = 0.0

        for hit in hits {
            var since = bar - hit
            if since < 0 { since += 4 }
            loudest = max(loudest, exp(-since * decay))
        }

        return loudest
    }

    // 120bpm, which is where most of a soundcheck lives.
    private static let beatSeconds = 60.0 / 120.0
    // The meter's own floor (ChannelMeterUIView.floorDb) - repeated as a
    // literal rather than referenced, so this file stays free of any
    // dependency on the view layer.
    private static let meterFloorDb = -60.0
    private static let meterCeilingDb = -3.0
}
