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

/**
 * One of this account's own banks: a name it chose and the channels it
 * put under it. Nothing to do with the console's own banks beyond the
 * fact that a new account's set is seeded from them - see
 * RemoteServer._stored_banks.
 */
data class CustomBank(
    val name: String,
    val channels: List<Int>
) : Serializable

/** A channel as the bank editor needs it: the number the desk calls it
 *  and whatever name is currently on it. Every channel on the console,
 *  not just the ones the current bank is pushing - picking from the lot
 *  is the whole point of the editor. */
data class BankChannel(
    val channel: Int,
    val name: String
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
    val phantom: Boolean = false,

    // Polarity on the channel input, as the console's own value: 0 is
    // normal and 1..3 are inverted. It is not a flag - a stereo channel
    // has four states, and which of 1/2/3 inverts which leg has never
    // been observed, so nothing here interprets them (see
    // docs/mixer_protocol/PROTOCOL.md, "The iPad app's parameter
    // dictionary").
    //
    // The number is carried rather than a boolean so that turning
    // polarity off and on again restores the state the desk had, instead
    // of flattening a stereo channel to 1. 0 when the console has not
    // reported it, the same reading as phantom above.
    val phase: Int = PHASE_NORMAL,

    // The alternate input slot: its own head-amp gain (null until the
    // console answers) and 48V. There is no alt trim - the trim sits
    // after the main/alt switch, so `trim` above covers both.
    val altGain: Double? = null,
    val altPhantom: Boolean = false,

    // Whether the channel is running on its alt input rather than main.
    val altIn: Boolean = false,

    // Whether there is an alt route patched to switch to at all - the
    // server works this out (see _alt_available in remote_server.py).
    // The alt controls stay disabled until it is.
    val altAvailable: Boolean = false
) {
    val phaseInverted: Boolean get() = phase != PHASE_NORMAL
}

// Polarity states. Only the two named ones are ever written by this app;
// the rest are values the desk may already hold and this app preserves.
const val PHASE_NORMAL = 0
const val PHASE_INVERTED = 1
