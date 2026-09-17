package com.clmix

import java.io.Serializable

// stereo says whether the bus has a pan axis at all. A mono aux sums its
// sends to one leg, so send_pan does nothing on it - the console accepts
// and echoes the write regardless, which is exactly why the server has to
// tell us rather than us discovering it by trying. Defaults true so an
// older server that omits the field behaves as it always did.
data class AuxBus(
    val index: Int,
    val name: String,
    val stereo: Boolean = true
) : Serializable

data class ChannelState(
    val channel: Int,
    val name: String,
    val level: Double?,
    val pan: Double?,
    // Muted *in the currently selected aux mix* only - the server maps
    // this to the console's per-send on/off flag, not the channel mute
    // that would cut the source for FOH and every other mix too.
    val muted: Boolean,
    // Whether this channel is a stereo pair, which decides if its meter
    // draws one bar or two. From the console's own channel modes list -
    // the right-hand meter address answers on mono channels too, so it
    // cannot be inferred from the meter data itself.
    val stereo: Boolean = false,

    // The channel's input stage, in dB: the analogue head-amp gain and
    // the digital trim behind it. Only ever sent in mixer mode - they
    // belong to the channel rather than to any one mix - and null until
    // the console has answered for this channel.
    val gain: Double? = null,
    val trim: Double? = null,

    // 48V on the channel's main input. False rather than null when the
    // console has not answered yet: phantom off is the safe reading, and
    // the button is a toggle with no third state to show.
    val phantom: Boolean = false
)
