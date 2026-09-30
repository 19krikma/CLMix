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
