import Foundation

/// `stereo` says whether the bus has a pan axis at all. A mono aux sums
/// its sends to one leg, so send_pan does nothing on it - the console
/// accepts and echoes the write regardless, which is exactly why the
/// server has to tell us rather than us discovering it by trying.
/// Defaults true so an older server that omits the field behaves as it
/// always did. Mirrors Android's Models.kt.
struct AuxBus: Identifiable, Hashable {
    let index: Int
    let name: String
    var stereo: Bool = true

    var id: Int { index }
}

/// Polarity states on a channel input. Only the two named ones are ever
/// written by this app; the rest are values the desk may already hold and
/// this app preserves. Mirrors Android's PHASE_NORMAL / PHASE_INVERTED.
enum PhaseState {
    static let normal = 0
    static let inverted = 1
}

/// The range a head-amp dial sweeps, in dB. One per parameter: the desk
/// stores gain over -20...+60 and trim over -40...+40, which the official
/// app's own parameter table confirms for gain and narrows for trim - see
/// docs/mixer_protocol/PROTOCOL.md, "Head-amp ranges". Used until a server
/// states its own at login; both dials used to share the union, -40...+60,
/// which gave gain 20 dB of travel the desk only ever clamps away.
enum HeadAmpRange {
    static let gain = -20.0...60.0
    static let trim = -40.0...40.0
}

struct ChannelState: Identifiable, Hashable {
    let channel: Int
    let name: String
    let level: Double?
    let pan: Double?
    // Muted *in the currently selected aux mix* only - the server maps
    // this to the console's per-send on/off flag, not the channel mute
    // that would cut the source for FOH and every other mix too. In
    // mixer mode it is the console's own channel mute instead.
    var muted: Bool
    // Whether this channel is a stereo pair, which decides if its meter
    // draws one bar or two. From the console's own channel modes list -
    // the right-hand meter address answers on mono channels too, so it
    // cannot be inferred from the meter data itself.
    var stereo: Bool = false

    // The channel's input stage, in dB: the analogue head-amp gain and
    // the digital trim behind it. Only ever sent in mixer mode - they
    // belong to the channel rather than to any one mix - and nil until
    // the console has answered for this channel.
    var gain: Double? = nil
    var trim: Double? = nil

    // 48V on the channel's main input. False rather than nil when the
    // console has not answered yet: phantom off is the safe reading, and
    // the button is a toggle with no third state to show.
    var phantom: Bool = false

    // Polarity on the channel input, as the console's own value: 0 is
    // normal and 1...3 are inverted. It is not a flag - a stereo channel
    // has four states, and which of 1/2/3 inverts which leg has never been
    // observed, so nothing here interprets them (see
    // docs/mixer_protocol/PROTOCOL.md, "The iPad app's parameter
    // dictionary").
    //
    // The number is carried rather than a Bool so that turning polarity
    // off and on again restores the state the desk had, instead of
    // flattening a stereo channel to 1. 0 when the console has not
    // reported it, the same reading as phantom above.
    var phase: Int = PhaseState.normal

    // The alternate input slot: its own head-amp gain (nil until the
    // console answers) and 48V. There is no alt trim - the trim sits
    // after the main/alt switch, so `trim` above covers both.
    var altGain: Double? = nil
    var altPhantom: Bool = false

    // Whether the channel is running on its alt input rather than main.
    var altIn: Bool = false

    // Whether there is an alt route patched to switch to at all - the
    // server works this out (see _alt_available in remote_server.py).
    // The alt controls stay disabled until it is.
    var altAvailable: Bool = false

    // Whether the channel's EQ is switched in, which lights the strip's
    // EQ button. Mixer mode only; nil until the console answers, or from
    // a server too old to send it.
    var eqIn: Bool? = nil

    // The whole EQ, drawn in miniature as the strip's EQ button. Mixer
    // mode only; nil from a server too old to send it.
    var eq: ChannelEq? = nil

    // Whether the compressor and the gate are in, which light the strip's
    // COMP and GATE buttons. Mixer mode only; nil until reported.
    var compIn: Bool? = nil
    var gateIn: Bool? = nil

    // What the COMP and GATE thumbnails draw: each section's in/out and
    // the settings that shape its static curve. Mixer mode only.
    var dynamics: ChannelDynamics? = nil

    var phaseInverted: Bool { phase != PhaseState.normal }

    var id: Int { channel }
}

/// One channel's post-fader meter reading. Null where the console
/// reported its no-signal sentinel; the right pair is null on a mono
/// channel. Mirrors Android's MeterLevels.
struct MeterLevels {
    let leftPeak: Double?
    let leftRms: Double?
    let rightPeak: Double?
    let rightRms: Double?
}

/// One of this account's own banks: a name it chose and the channels it
/// put under it. Nothing to do with the console's own banks beyond the
/// fact that a new account's set is seeded from them - see
/// RemoteServer._stored_banks.
struct CustomBank: Identifiable, Equatable {
    var name: String
    var channels: [Int]

    /// By name, since that is what the server keys a bank on and what
    /// the picker shows. Renaming one is therefore a new identity, which
    /// is right: the row has become a different bank as far as anything
    /// watching the list is concerned.
    var id: String { name }
}

/// A channel as the bank editor needs it: the number the desk calls it
/// and whatever name is currently on it. Every channel on the console,
/// not just the ones the current bank is pushing - picking from the lot
/// is the whole point of the editor.
struct BankChannel: Identifiable, Equatable {
    let channel: Int
    let name: String

    var id: Int { channel }
}

/// The channel EQ's shape and ranges, as the console's own parameter table
/// states them (docs/mixer_protocol/PROTOCOL.md). Frequencies are Hz on
/// the wire even where the desk's dial reads kHz. The server clamps every
/// write to these too; they are here so the controls cannot sweep past
/// them. Mirrors Android's EQ_* constants.
enum EqRange {
    static let bandCount = 4
    static let freq = 20.0...20000.0
    static let gain = -18.0...18.0
    static let q = 0.1...20.0
}

/// One parametric band of a channel's EQ, numbered as the console numbers
/// it (1...EqRange.bandCount). Every value is nil until the console has
/// reported it - a band with no frequency yet is left off the graph rather
/// than drawn somewhere the desk does not have it.
///
/// `curve` is the console's own enum, 1...4. Only 1 has ever been seen on
/// a desk, so the app draws 1 as a bell and never writes the value - see
/// EqResponse for how the others are drawn.
struct EqBand: Hashable {
    let band: Int
    var on: Bool? = nil
    var freq: Double? = nil
    var gain: Double? = nil
    var q: Double? = nil
    var curve: Int? = nil
}

/// A high- or low-pass filter ahead of the EQ: switched in, and where.
struct EqFilter: Hashable {
    var isIn: Bool? = nil
    var freq: Double? = nil
}

/// Everything the EQ screen shows for one input channel.
struct ChannelEq: Hashable {
    let channel: Int
    var eqIn: Bool? = nil
    var bands: [EqBand] = (1...EqRange.bandCount).map { EqBand(band: $0) }
    var loFilter = EqFilter()
    var hiFilter = EqFilter()
}

/// One EQ write, as the set_eq action carries it. `lo` picks the
/// high-pass (the console's lo_filter - it cuts lows) over the low-pass.
enum EqWrite {
    case eqIn(Bool)
    case bandOn(band: Int, Bool)
    case freq(band: Int, Double)
    case gain(band: Int, Double)
    case q(band: Int, Double)
    case filterIn(lo: Bool, Bool)
    case filterFreq(lo: Bool, Double)
}

/// One section of a channel's dynamics - the compressor or the gate - as
/// the Dynamics screen shows it. Values are keyed by DynParam.key; a value
/// the console has not reported is absent.
///
/// `mode` is the console's own enum for what the section is running
/// (Dynamics 1: DynMode; Dynamics 2, the gate slot: GateMode) and `knee`
/// the compressor's knee. Both are shown, never written - the mode is
/// changed on the console. Mirrors Android's DynSection.
struct DynSection: Hashable {
    var isIn: Bool? = nil
    var values: [String: Double] = [:]
    var mode: Int? = nil
    var knee: Int? = nil
}

struct ChannelDynamics: Hashable {
    let channel: Int
    var comp = DynSection()
    var gate = DynSection()
}

/// The static curve each section makes: level out for level in, in dB.
/// No knee, attack or release - those shape time rather than level. Shared
/// by the Dynamics screen's graph and the strip thumbnails. Mirrors
/// Android's DynResponse.
enum DynResponse {
    /// Dynamics 2, by its mode (GateMode): a gate pulls the level down by
    /// its range below its threshold, a ducker by its range above it
    /// (drawn as if keyed from the channel itself), and a compressor is the
    /// compressor's curve.
    static func gate(_ section: DynSection, _ input: Double) -> Double {
        let mode = GateMode(rawValue: section.mode ?? 0) ?? .gate
        if mode == .compressor { return comp(section, input) }

        guard section.isIn == true, let thresh = section.values["thresh"] else { return input }
        let acts = mode == .duck ? input >= thresh : input < thresh
        return acts ? input - (section.values["range"] ?? 0) : input
    }

    /// The compressor: bent over by its ratio above its threshold, then
    /// lifted by its makeup gain.
    static func comp(_ section: DynSection, _ input: Double) -> Double {
        guard section.isIn == true, let thresh = section.values["thresh"] else { return input }
        let ratio = max(1, section.values["ratio"] ?? 1)
        let compressed = input > thresh ? thresh + (input - thresh) / ratio : input
        return compressed + (section.values["gain"] ?? 0)
    }
}

/// How a dynamics dial's value reads. Mirrors Android's DynKind.
enum DynKind { case db, ratio, time, freq, step }

/// One dial on the Dynamics screen. Ranges are the console's own parameter
/// table (docs/mixer_protocol/PROTOCOL.md); times are seconds on the wire,
/// crossovers Hz. Log dials turn in octaves rather than in units - an
/// attack running 20 us...100 ms would otherwise spend all its travel on
/// the slow end. Dynamics 1's three modes show different sets of these
/// (DynMode.pages); band 1's dials are the compressor's own. Mirrors
/// Android's DynParam.
enum DynParam: CaseIterable, Hashable {
    case compThresh, compRatio, compAttack, compRelease, compGain
    case compThresh2, compRatio2, compAttack2, compRelease2, compGain2
    case compThresh3, compRatio3, compAttack3, compRelease3, compGain3
    case compAllThresh, compAllGain, compLpXover, compHpXover
    case compDesserFreq, compDesserWidth
    case gateThresh, gateRange, gateAttack, gateHold, gateRelease
    // Dynamics 2 as a compressor: the console's comp_ratio_4 and comp_gain_4.
    case gateRatio, gateGain

    private struct Spec {
        let key: String
        let label: String
        let range: ClosedRange<Double>
        let log: Bool
        let rate: Double
        let kind: DynKind
    }

    private var spec: Spec {
        switch self {
        case .compThresh: return Spec(key: "thresh", label: "Thresh", range: -60...0, log: false, rate: 0.6, kind: .db)
        case .compRatio: return Spec(key: "ratio", label: "Ratio", range: 1...50, log: true, rate: 0.2, kind: .ratio)
        case .compAttack: return Spec(key: "attack", label: "Attack", range: 0.00002...0.1, log: true, rate: 0.25, kind: .time)
        case .compRelease: return Spec(key: "release", label: "Release", range: 0.001...10, log: true, rate: 0.25, kind: .time)
        case .compGain: return Spec(key: "gain", label: "Gain", range: 0...40, log: false, rate: 0.6, kind: .db)
        case .compThresh2: return Spec(key: "thresh_2", label: "Thresh", range: -60...0, log: false, rate: 0.6, kind: .db)
        case .compRatio2: return Spec(key: "ratio_2", label: "Ratio", range: 1...50, log: true, rate: 0.2, kind: .ratio)
        case .compAttack2: return Spec(key: "attack_2", label: "Attack", range: 0.00002...0.1, log: true, rate: 0.25, kind: .time)
        case .compRelease2: return Spec(key: "release_2", label: "Release", range: 0.001...10, log: true, rate: 0.25, kind: .time)
        case .compGain2: return Spec(key: "gain_2", label: "Gain", range: 0...40, log: false, rate: 0.6, kind: .db)
        case .compThresh3: return Spec(key: "thresh_3", label: "Thresh", range: -60...0, log: false, rate: 0.6, kind: .db)
        case .compRatio3: return Spec(key: "ratio_3", label: "Ratio", range: 1...50, log: true, rate: 0.2, kind: .ratio)
        case .compAttack3: return Spec(key: "attack_3", label: "Attack", range: 0.00002...0.1, log: true, rate: 0.25, kind: .time)
        case .compRelease3: return Spec(key: "release_3", label: "Release", range: 0.001...10, log: true, rate: 0.25, kind: .time)
        case .compGain3: return Spec(key: "gain_3", label: "Gain", range: 0...40, log: false, rate: 0.6, kind: .db)
        case .compAllThresh: return Spec(key: "all_thresh", label: "Thresh", range: -60...0, log: false, rate: 0.6, kind: .db)
        case .compAllGain: return Spec(key: "all_gain", label: "Gain", range: 0...40, log: false, rate: 0.6, kind: .db)
        case .compLpXover: return Spec(key: "lp_xover", label: "Low X", range: 20...20000, log: true, rate: 0.25, kind: .freq)
        case .compHpXover: return Spec(key: "hp_xover", label: "High X", range: 20...20000, log: true, rate: 0.25, kind: .freq)
        // 0-255 steps on the wire; how they map to Hz has not been captured.
        case .compDesserFreq: return Spec(key: "desser_freq", label: "Freq", range: 0...255, log: false, rate: 2, kind: .step)
        case .compDesserWidth: return Spec(key: "desser_width", label: "Width", range: 0...255, log: false, rate: 2, kind: .step)
        case .gateThresh: return Spec(key: "thresh", label: "Thresh", range: -60...0, log: false, rate: 0.6, kind: .db)
        case .gateRange: return Spec(key: "range", label: "Range", range: 0...90, log: false, rate: 0.8, kind: .db)
        case .gateAttack: return Spec(key: "attack", label: "Attack", range: 0.00005...0.1, log: true, rate: 0.25, kind: .time)
        case .gateHold: return Spec(key: "hold", label: "Hold", range: 0.002...2, log: true, rate: 0.25, kind: .time)
        case .gateRelease: return Spec(key: "release", label: "Release", range: 0.005...10, log: true, rate: 0.25, kind: .time)
        case .gateRatio: return Spec(key: "ratio", label: "Ratio", range: 1...50, log: true, rate: 0.2, kind: .ratio)
        case .gateGain: return Spec(key: "gain", label: "Gain", range: 0...40, log: false, rate: 0.6, kind: .db)
        }
    }

    /// Every parameter of each section, for parsing.
    static let comp: [DynParam] = allCases.filter { $0.isComp }
    static let gate: [DynParam] = [.gateThresh, .gateRange, .gateAttack, .gateHold, .gateRelease,
                                   .gateRatio, .gateGain]

    /// The threshold, ratio, attack, release and gain of one band.
    static func band(_ n: Int) -> [DynParam] {
        switch n {
        case 2: return [.compThresh2, .compRatio2, .compAttack2, .compRelease2, .compGain2]
        case 3: return [.compThresh3, .compRatio3, .compAttack3, .compRelease3, .compGain3]
        default: return [.compThresh, .compRatio, .compAttack, .compRelease, .compGain]
        }
    }

    var isComp: Bool { !DynParam.gate.contains(self) }
    var key: String { spec.key }
    var label: String { spec.label }
    var range: ClosedRange<Double> { spec.range }
    var log: Bool { spec.log }
    var rate: Double { spec.rate }

    /// The set_dyn param name: comp_thresh, comp_ratio_2, gate_hold and so on.
    var wireName: String { (isComp ? "comp_" : "gate_") + key }

    func format(_ value: Double?) -> String {
        guard let value else { return "--" }
        switch spec.kind {
        case .ratio: return String(format: "%.1f:1", value)
        case .time:
            if value < 0.01 { return String(format: "%.2f ms", value * 1000) }
            if value < 1 { return String(format: "%.0f ms", value * 1000) }
            return String(format: "%.2f s", value)
        case .freq:
            return value < 1000 ? String(format: "%.0f Hz", value)
                : String(format: "%.2f kHz", value / 1000)
        case .step: return String(format: "%.0f", value)
        case .db: return String(format: "%.1f dB", value)
        }
    }
}

/// Dynamics 1's mode, the console's comp-multiband-desser (captured on
/// channel 43, 2026-10-24). Each page is a set of dials; band is the
/// multiband band a page edits (nil for one that is not a band), and
/// graphBand the band whose curve the graph draws. Mirrors Android's
/// DynMode.
enum DynMode: Int {
    case compressor = 0, multiband = 1, deEsser = 2

    struct Page {
        let tab: String?
        let params: [DynParam]
        let band: Int?
        let graphBand: Int
    }

    var title: String {
        switch self {
        case .compressor: return "COMPRESSOR"
        case .multiband: return "MULTIBAND"
        case .deEsser: return "DE-ESSER"
        }
    }

    var pages: [Page] {
        switch self {
        case .compressor:
            return [Page(tab: nil, params: DynParam.band(1), band: nil, graphBand: 1)]
        case .multiband:
            return [
                Page(tab: "LOW", params: DynParam.band(1), band: 1, graphBand: 1),
                Page(tab: "MID", params: DynParam.band(2), band: 2, graphBand: 2),
                Page(tab: "HIGH", params: DynParam.band(3), band: 3, graphBand: 3),
                Page(tab: "ALL", params: [.compAllThresh, .compAllGain, .compLpXover, .compHpXover],
                     band: nil, graphBand: 1),
            ]
        case .deEsser:
            // Band 3 alone, with bands 1 and 2 switched out by the desk.
            return [Page(tab: nil,
                         params: [.compThresh3, .compRatio3, .compAttack3, .compRelease3,
                                  .compDesserFreq, .compDesserWidth],
                         band: nil, graphBand: 3)]
        }
    }
}

/// Dynamics 2's mode, the console's gate-duck-comp (captured on channel
/// 22, 2026-10-25). Gate and ducker share the gate's five settings; the
/// compressor swaps range and hold for a ratio and makeup gain. Mirrors
/// Android's GateMode.
enum GateMode: Int {
    case gate = 0, duck = 1, compressor = 2

    var title: String {
        switch self {
        case .gate: return "GATE"
        case .duck: return "DUCKER"
        case .compressor: return "COMPRESSOR"
        }
    }

    /// The letter on the graph's threshold handle.
    var handle: String {
        switch self {
        case .gate: return "G"
        case .duck: return "D"
        case .compressor: return "C"
        }
    }

    var pages: [DynMode.Page] {
        let params: [DynParam] = self == .compressor
            ? [.gateThresh, .gateRatio, .gateAttack, .gateRelease, .gateGain]
            : [.gateThresh, .gateRange, .gateAttack, .gateHold, .gateRelease]
        return [DynMode.Page(tab: nil, params: params, band: nil, graphBand: 1)]
    }
}

extension DynSection {
    /// One band of the compressor as a plain section - thresh, ratio and
    /// gain under the names DynResponse reads - so the graph can draw any
    /// band the way it draws the single-band compressor.
    func band(_ n: Int) -> DynSection {
        if n == 1 { return self }
        var out = self
        out.isIn = isIn == true && values["band_in_\(n)"] != 0
        out.values = [:]
        for name in ["thresh", "ratio", "gain"] {
            if let value = values["\(name)_\(n)"] { out.values[name] = value }
        }
        return out
    }
}

/// One dynamics write, as the set_dyn action carries it.
enum DynWrite {
    case compIn(Bool)
    case gateIn(Bool)
    /// One multiband band (1...3) in or out.
    case bandIn(Int, Bool)
    case value(DynParam, Double)
}

/// A preset this account can see: its own, one shared with it, or a
/// public one - see the server's PresetStore. `owner` is nil for presets
/// saved before owners existed. `sharedWith` is only ever filled in on
/// the account's own presets. An empty `serverID` means a server from
/// before owners, which lists and loads by name alone. Mirrors Android's
/// Preset.
struct Preset: Identifiable, Equatable {
    let serverID: String
    let name: String
    let owner: String?
    let mine: Bool
    let isPublic: Bool
    let sharedWithMe: Bool
    let sharedWith: [String]
    /// The aux it was saved from, and only loads onto - nil for a preset
    /// from before auxes were recorded, which loads onto any.
    var aux: Int? = nil
    var auxName: String? = nil

    /// Stable across refreshes; names are unique on an old server.
    var id: String { serverID.isEmpty ? name : serverID }

    /// The line under the name in the Load sheet.
    var subtitle: String {
        ownership + (aux == nil ? " \u{00B7} Any aux" : "")
    }

    private var ownership: String {
        if mine && isPublic { return "Yours \u{00B7} Public" }
        if mine && !sharedWith.isEmpty {
            return "Yours \u{00B7} Shared with \(sharedWith.joined(separator: ", "))"
        }
        if mine { return "Yours \u{00B7} Private" }
        // Someone else's, shared with this account or public: who it
        // belongs to is what matters.
        if let owner { return "Owner: \(owner)" }
        return "Public"
    }
}
