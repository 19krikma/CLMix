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

    // Polarity invert on the channel input. False when the console has
    // not reported it, the same reading as phantom above.
    var phase: Bool = false

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
