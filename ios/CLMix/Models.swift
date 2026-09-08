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
    // that would cut the source for FOH and every other mix too.
    var muted: Bool
    // Whether this channel is a stereo pair, which decides if its meter
    // draws one bar or two. From the console's own channel modes list -
    // the right-hand meter address answers on mono channels too, so it
    // cannot be inferred from the meter data itself.
    var stereo: Bool = false

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
