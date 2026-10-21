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
/// `mode` is the console's own enum for what the section is running (the
/// compressor: single-band, multiband or de-esser; the gate slot: gate,
/// duck or compressor) and `knee` the compressor's knee. Neither has ever
/// been seen to mean anything particular on a desk, so they are shown,
/// never written. Mirrors Android's DynSection.
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
    /// The gate: pulled down by its range below its threshold.
    static func gate(_ section: DynSection, _ input: Double) -> Double {
        guard section.isIn == true, let thresh = section.values["thresh"], input < thresh else {
            return input
        }
        return input - (section.values["range"] ?? 0)
    }

    /// The compressor: bent over by its ratio above its threshold, then
    /// lifted by its makeup gain.
    static func comp(_ section: DynSection, _ input: Double) -> Double {
        guard section.isIn == true, let thresh = section.values["thresh"] else { return input }
        let ratio = max(1, section.values["ratio"] ?? 1)
        let compressed = input > thresh ? thresh + (input - thresh) / ratio : input
        return compressed + (section.values["gain"] ?? 0)
    }

    /// Both, in the console's order: gate first.
    static func both(_ dynamics: ChannelDynamics, _ input: Double) -> Double {
        comp(dynamics.comp, gate(dynamics.gate, input))
    }
}

/// One dial on the Dynamics screen. Ranges are the console's own parameter
/// table (docs/mixer_protocol/PROTOCOL.md); times are seconds on the wire.
/// Log dials turn in octaves rather than in units - an attack running
/// 20 us...100 ms would otherwise spend all its travel on the slow end.
/// Mirrors Android's DynParam.
enum DynParam: CaseIterable, Hashable {
    case compThresh, compRatio, compAttack, compRelease, compGain
    case gateThresh, gateRange, gateAttack, gateHold, gateRelease

    static let comp: [DynParam] = [.compThresh, .compRatio, .compAttack, .compRelease, .compGain]
    static let gate: [DynParam] = [.gateThresh, .gateRange, .gateAttack, .gateHold, .gateRelease]

    var isComp: Bool { DynParam.comp.contains(self) }

    var key: String {
        switch self {
        case .compThresh, .gateThresh: return "thresh"
        case .compRatio: return "ratio"
        case .compAttack, .gateAttack: return "attack"
        case .compRelease, .gateRelease: return "release"
        case .compGain: return "gain"
        case .gateRange: return "range"
        case .gateHold: return "hold"
        }
    }

    /// The set_dyn param name: comp_thresh, gate_hold and so on.
    var wireName: String { (isComp ? "comp_" : "gate_") + key }

    var label: String { key == "thresh" ? "Thresh" : key.capitalized }

    var range: ClosedRange<Double> {
        switch self {
        case .compThresh, .gateThresh: return -60...0
        case .compRatio: return 1...50
        case .compAttack: return 0.00002...0.1
        case .compRelease: return 0.001...10
        case .compGain: return 0...40
        case .gateRange: return 0...90
        case .gateAttack: return 0.00005...0.1
        case .gateHold: return 0.002...2
        case .gateRelease: return 0.005...10
        }
    }

    var log: Bool {
        switch key {
        case "ratio", "attack", "release", "hold": return true
        default: return false
        }
    }

    var rate: Double {
        switch key {
        case "thresh", "gain": return 0.6
        case "range": return 0.8
        case "ratio": return 0.2
        default: return 0.25
        }
    }

    func format(_ value: Double?) -> String {
        guard let value else { return "--" }
        switch key {
        case "ratio": return String(format: "%.1f:1", value)
        case "attack", "release", "hold":
            if value < 0.01 { return String(format: "%.2f ms", value * 1000) }
            if value < 1 { return String(format: "%.0f ms", value * 1000) }
            return String(format: "%.2f s", value)
        default: return String(format: "%.1f dB", value)
        }
    }
}

/// One dynamics write, as the set_dyn action carries it.
enum DynWrite {
    case compIn(Bool)
    case gateIn(Bool)
    case value(DynParam, Double)
}
