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
    val altAvailable: Boolean = false,

    // Whether the channel's EQ is switched in, which lights the strip's
    // EQ button. Mixer mode only; null until the console answers, or
    // from a server too old to send it.
    val eqIn: Boolean? = null,

    // The whole EQ, drawn in miniature as the strip's EQ button. Mixer
    // mode only; null from a server too old to send it.
    val eq: ChannelEq? = null,

    // Whether the compressor and the gate are in, which light the strip's
    // COMP and GATE buttons. Mixer mode only; null until reported.
    val compIn: Boolean? = null,
    val gateIn: Boolean? = null,

    // What the COMP and GATE thumbnails draw: each section's in/out and
    // the settings that shape its static curve. Mixer mode only.
    val dynamics: ChannelDynamics? = null
) {
    val phaseInverted: Boolean get() = phase != PHASE_NORMAL
}

// Polarity states. Only the two named ones are ever written by this app;
// the rest are values the desk may already hold and this app preserves.
const val PHASE_NORMAL = 0
const val PHASE_INVERTED = 1

/**
 * One parametric band of a channel's EQ, numbered as the console numbers
 * it (1..EQ_BAND_COUNT). Every value is null until the console has
 * reported it - a band with no frequency yet is left off the graph rather
 * than drawn somewhere the desk does not have it.
 *
 * curve is the console's own enum, 1..4. Only 1 has ever been seen on a
 * desk, so the app draws 1 as a bell and never writes the value - see
 * EqResponse for how the others are drawn.
 */
data class EqBand(
    val band: Int,
    val on: Boolean? = null,
    val freq: Double? = null,
    val gain: Double? = null,
    val q: Double? = null,
    val curve: Int? = null
)

/** A high- or low-pass filter ahead of the EQ: switched in, and where. */
data class EqFilter(
    val isIn: Boolean? = null,
    val freq: Double? = null
)

/** Everything the EQ screen shows for one input channel. */
data class ChannelEq(
    val channel: Int,
    val eqIn: Boolean? = null,
    val bands: List<EqBand> = (1..EQ_BAND_COUNT).map { EqBand(it) },
    val loFilter: EqFilter = EqFilter(),
    val hiFilter: EqFilter = EqFilter()
)

// The channel EQ's shape and ranges, as the console's own parameter table
// states them (docs/mixer_protocol/PROTOCOL.md). Frequencies are Hz on the
// wire even where the desk's dial reads kHz. The server clamps every write
// to these too; they are here so the controls cannot sweep past them.
const val EQ_BAND_COUNT = 4
val EQ_FREQ_RANGE = 20.0..20000.0
val EQ_GAIN_RANGE = -18.0..18.0
val EQ_Q_RANGE = 0.1..20.0

/**
 * One section of a channel's dynamics - the compressor or the gate - as
 * the Dynamics screen shows it. Values are keyed by the server's names
 * (DynParam.key); a value the console has not reported is absent.
 *
 * mode is the console's own enum for what the section is running (the
 * compressor: single-band, multiband or de-esser; the gate slot: gate,
 * duck or compressor) and knee the compressor's knee. Neither has ever
 * been seen to mean anything particular on a desk, so they are shown,
 * never written.
 */
data class DynSection(
    val isIn: Boolean? = null,
    val values: Map<String, Double> = emptyMap(),
    val mode: Int? = null,
    val knee: Int? = null
)

data class ChannelDynamics(
    val channel: Int,
    val comp: DynSection = DynSection(),
    val gate: DynSection = DynSection()
)

/**
 * The static curve each section makes: level out for level in, in dB. No
 * knee, attack or release - those shape time rather than level - so this
 * is the picture the console's own dynamics graph draws, not a model of
 * it. Shared by the Dynamics screen's graph and the strip thumbnails.
 */
object DynResponse {
    /** The gate: pulled down by its range below its threshold. */
    fun gate(section: DynSection, input: Double): Double {
        val thresh = section.values["thresh"]
        if (section.isIn != true || thresh == null || input >= thresh) return input
        return input - (section.values["range"] ?: 0.0)
    }

    /** The compressor: bent over by its ratio above its threshold, then
     *  lifted by its makeup gain. */
    fun comp(section: DynSection, input: Double): Double {
        val thresh = section.values["thresh"]
        if (section.isIn != true || thresh == null) return input

        val ratio = (section.values["ratio"] ?: 1.0).coerceAtLeast(1.0)
        val compressed = if (input > thresh) thresh + (input - thresh) / ratio else input
        return compressed + (section.values["gain"] ?: 0.0)
    }

    /** Both, in the console's order: gate first. */
    fun both(dynamics: ChannelDynamics, input: Double): Double =
        comp(dynamics.comp, gate(dynamics.gate, input))
}

/**
 * One dial on the Dynamics screen. Ranges are the console's own parameter
 * table (docs/mixer_protocol/PROTOCOL.md); times are seconds on the wire.
 * log dials turn in octaves rather than in units - an attack running
 * 20 us..100 ms would otherwise spend all its travel on the slow end.
 */
enum class DynParam(
    val key: String,
    val label: String,
    val range: ClosedFloatingPointRange<Double>,
    val log: Boolean,
    val rate: Double,
    val isComp: Boolean
) {
    COMP_THRESH("thresh", "Thresh", -60.0..0.0, false, 0.6, true),
    COMP_RATIO("ratio", "Ratio", 1.0..50.0, true, 0.2, true),
    COMP_ATTACK("attack", "Attack", 0.00002..0.1, true, 0.25, true),
    COMP_RELEASE("release", "Release", 0.001..10.0, true, 0.25, true),
    COMP_GAIN("gain", "Gain", 0.0..40.0, false, 0.6, true),
    GATE_THRESH("thresh", "Thresh", -60.0..0.0, false, 0.6, false),
    GATE_RANGE("range", "Range", 0.0..90.0, false, 0.8, false),
    GATE_ATTACK("attack", "Attack", 0.00005..0.1, true, 0.25, false),
    GATE_HOLD("hold", "Hold", 0.002..2.0, true, 0.25, false),
    GATE_RELEASE("release", "Release", 0.005..10.0, true, 0.25, false);

    /** The set_dyn param name: comp_thresh, gate_hold and so on. */
    val wireName: String get() = (if (isComp) "comp_" else "gate_") + key

    fun format(value: Double?): String {
        if (value == null) return "--"

        return when (key) {
            "ratio" -> String.format("%.1f:1", value)
            "attack", "release", "hold" -> when {
                value < 0.01 -> String.format("%.2f ms", value * 1000)
                value < 1 -> String.format("%.0f ms", value * 1000)
                else -> String.format("%.2f s", value)
            }
            else -> String.format("%.1f dB", value)
        }
    }

    companion object {
        val comp = values().filter { it.isComp }
        val gate = values().filter { !it.isComp }
    }
}
