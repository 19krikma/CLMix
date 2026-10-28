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
 * mode is the console's own enum for what the section is running
 * (Dynamics 1: DynMode; Dynamics 2, the gate slot: GateMode) and knee the
 * compressor's knee. Both are shown, never written - the mode is changed
 * on the desk.
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
    /** Dynamics 2, by its mode (GateMode): a gate pulls the level down
     *  by its range below its threshold, a ducker by its range above it
     *  (drawn as if keyed from the channel itself), and a compressor is
     *  the compressor's curve. */
    fun gate(section: DynSection, input: Double): Double {
        if (GateMode.of(section.mode) == GateMode.COMPRESSOR) return comp(section, input)

        val thresh = section.values["thresh"]
        if (section.isIn != true || thresh == null) return input

        val duck = GateMode.of(section.mode) == GateMode.DUCK
        val acts = if (duck) input >= thresh else input < thresh
        return if (acts) input - (section.values["range"] ?: 0.0) else input
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
}

/** How a dynamics dial's value reads. */
enum class DynKind { DB, RATIO, TIME, FREQ, STEP }

/**
 * One dial on the Dynamics screen. Ranges are the console's own parameter
 * table (docs/mixer_protocol/PROTOCOL.md); times are seconds on the wire,
 * crossovers Hz. log dials turn in octaves rather than in units - an
 * attack running 20 us..100 ms would otherwise spend all its travel on
 * the slow end.
 *
 * Dynamics 1 has three modes (DynMode); which dials each one shows is
 * DynMode.pages. Band 1's dials are the compressor's own.
 */
enum class DynParam(
    val key: String,
    val label: String,
    val range: ClosedFloatingPointRange<Double>,
    val log: Boolean,
    val rate: Double,
    val isComp: Boolean,
    val kind: DynKind
) {
    COMP_THRESH("thresh", "Thresh", -60.0..0.0, false, 0.6, true, DynKind.DB),
    COMP_RATIO("ratio", "Ratio", 1.0..50.0, true, 0.2, true, DynKind.RATIO),
    COMP_ATTACK("attack", "Attack", 0.00002..0.1, true, 0.25, true, DynKind.TIME),
    COMP_RELEASE("release", "Release", 0.001..10.0, true, 0.25, true, DynKind.TIME),
    COMP_GAIN("gain", "Gain", 0.0..40.0, false, 0.6, true, DynKind.DB),
    COMP_THRESH_2("thresh_2", "Thresh", -60.0..0.0, false, 0.6, true, DynKind.DB),
    COMP_RATIO_2("ratio_2", "Ratio", 1.0..50.0, true, 0.2, true, DynKind.RATIO),
    COMP_ATTACK_2("attack_2", "Attack", 0.00002..0.1, true, 0.25, true, DynKind.TIME),
    COMP_RELEASE_2("release_2", "Release", 0.001..10.0, true, 0.25, true, DynKind.TIME),
    COMP_GAIN_2("gain_2", "Gain", 0.0..40.0, false, 0.6, true, DynKind.DB),
    COMP_THRESH_3("thresh_3", "Thresh", -60.0..0.0, false, 0.6, true, DynKind.DB),
    COMP_RATIO_3("ratio_3", "Ratio", 1.0..50.0, true, 0.2, true, DynKind.RATIO),
    COMP_ATTACK_3("attack_3", "Attack", 0.00002..0.1, true, 0.25, true, DynKind.TIME),
    COMP_RELEASE_3("release_3", "Release", 0.001..10.0, true, 0.25, true, DynKind.TIME),
    COMP_GAIN_3("gain_3", "Gain", 0.0..40.0, false, 0.6, true, DynKind.DB),
    COMP_ALL_THRESH("all_thresh", "Thresh", -60.0..0.0, false, 0.6, true, DynKind.DB),
    COMP_ALL_GAIN("all_gain", "Gain", 0.0..40.0, false, 0.6, true, DynKind.DB),
    COMP_LP_XOVER("lp_xover", "Low X", 20.0..20000.0, true, 0.25, true, DynKind.FREQ),
    COMP_HP_XOVER("hp_xover", "High X", 20.0..20000.0, true, 0.25, true, DynKind.FREQ),
    // 0-255 steps on the wire; how they map to Hz has not been captured.
    COMP_DESSER_FREQ("desser_freq", "Freq", 0.0..255.0, false, 2.0, true, DynKind.STEP),
    COMP_DESSER_WIDTH("desser_width", "Width", 0.0..255.0, false, 2.0, true, DynKind.STEP),
    GATE_THRESH("thresh", "Thresh", -60.0..0.0, false, 0.6, false, DynKind.DB),
    GATE_RANGE("range", "Range", 0.0..90.0, false, 0.8, false, DynKind.DB),
    GATE_ATTACK("attack", "Attack", 0.00005..0.1, true, 0.25, false, DynKind.TIME),
    GATE_HOLD("hold", "Hold", 0.002..2.0, true, 0.25, false, DynKind.TIME),
    GATE_RELEASE("release", "Release", 0.005..10.0, true, 0.25, false, DynKind.TIME),
    // Dynamics 2 as a compressor: the desk's comp_ratio_4 and comp_gain_4.
    GATE_RATIO("ratio", "Ratio", 1.0..50.0, true, 0.2, false, DynKind.RATIO),
    GATE_GAIN("gain", "Gain", 0.0..40.0, false, 0.6, false, DynKind.DB);

    /** The set_dyn param name: comp_thresh, comp_ratio_2, gate_hold and so on. */
    val wireName: String get() = (if (isComp) "comp_" else "gate_") + key

    fun format(value: Double?): String {
        if (value == null) return "--"

        return when (kind) {
            DynKind.RATIO -> String.format("%.1f:1", value)
            DynKind.TIME -> when {
                value < 0.01 -> String.format("%.2f ms", value * 1000)
                value < 1 -> String.format("%.0f ms", value * 1000)
                else -> String.format("%.2f s", value)
            }
            DynKind.FREQ ->
                if (value < 1000) String.format("%.0f Hz", value)
                else String.format("%.2f kHz", value / 1000)
            DynKind.STEP -> String.format("%.0f", value)
            DynKind.DB -> String.format("%.1f dB", value)
        }
    }

    companion object {
        val comp = values().filter { it.isComp }
        val gate = values().filter { !it.isComp }

        /** The threshold, ratio, attack, release and gain of one band. */
        fun band(n: Int): List<DynParam> = when (n) {
            2 -> listOf(COMP_THRESH_2, COMP_RATIO_2, COMP_ATTACK_2, COMP_RELEASE_2, COMP_GAIN_2)
            3 -> listOf(COMP_THRESH_3, COMP_RATIO_3, COMP_ATTACK_3, COMP_RELEASE_3, COMP_GAIN_3)
            else -> listOf(COMP_THRESH, COMP_RATIO, COMP_ATTACK, COMP_RELEASE, COMP_GAIN)
        }
    }
}

/**
 * Dynamics 1's mode, the console's comp-multiband-desser (captured on
 * channel 43, 2026-10-24). Each page is a set of dials; band is the
 * multiband band a page edits (null for one that is not a band), and
 * graphBand the band whose curve the graph draws.
 */
enum class DynMode(val wire: Int, val title: String) {
    COMPRESSOR(0, "COMPRESSOR"),
    MULTIBAND(1, "MULTIBAND"),
    DE_ESSER(2, "DE-ESSER");

    class Page(val tab: String?, val params: List<DynParam>, val band: Int?, val graphBand: Int)

    val pages: List<Page>
        get() = when (this) {
            COMPRESSOR -> listOf(Page(null, DynParam.band(1), null, 1))
            MULTIBAND -> listOf(
                Page("LOW", DynParam.band(1), 1, 1),
                Page("MID", DynParam.band(2), 2, 2),
                Page("HIGH", DynParam.band(3), 3, 3),
                Page(
                    "ALL",
                    listOf(DynParam.COMP_ALL_THRESH, DynParam.COMP_ALL_GAIN,
                        DynParam.COMP_LP_XOVER, DynParam.COMP_HP_XOVER),
                    null, 1
                )
            )
            // Band 3 alone, with bands 1 and 2 switched out by the desk.
            DE_ESSER -> listOf(
                Page(
                    null,
                    listOf(DynParam.COMP_THRESH_3, DynParam.COMP_RATIO_3,
                        DynParam.COMP_ATTACK_3, DynParam.COMP_RELEASE_3,
                        DynParam.COMP_DESSER_FREQ, DynParam.COMP_DESSER_WIDTH),
                    null, 3
                )
            )
        }

    companion object {
        fun of(wire: Int?): DynMode? = values().firstOrNull { it.wire == wire }
    }
}

/**
 * Dynamics 2's mode, the console's gate-duck-comp (captured on channel 22,
 * 2026-10-25). Gate and ducker share the gate's five settings; the
 * compressor swaps range and hold for a ratio and makeup gain.
 */
enum class GateMode(val wire: Int, val title: String, val handle: String) {
    GATE(0, "GATE", "G"),
    DUCK(1, "DUCKER", "D"),
    COMPRESSOR(2, "COMPRESSOR", "C");

    val pages: List<DynMode.Page>
        get() = listOf(
            DynMode.Page(
                null,
                if (this == COMPRESSOR) {
                    listOf(DynParam.GATE_THRESH, DynParam.GATE_RATIO, DynParam.GATE_ATTACK,
                        DynParam.GATE_RELEASE, DynParam.GATE_GAIN)
                } else {
                    listOf(DynParam.GATE_THRESH, DynParam.GATE_RANGE, DynParam.GATE_ATTACK,
                        DynParam.GATE_HOLD, DynParam.GATE_RELEASE)
                },
                null, 1
            )
        )

    companion object {
        fun of(wire: Int?): GateMode? = values().firstOrNull { it.wire == wire }
    }
}

/**
 * One band of the compressor as a plain section - thresh, ratio and gain
 * under the names DynResponse reads - so the graph can draw any band the
 * way it draws the single-band compressor.
 */
fun DynSection.band(n: Int): DynSection {
    if (n == 1) return this
    val v = values
    return copy(
        isIn = isIn == true && v["band_in_$n"] != 0.0,
        values = buildMap {
            v["thresh_$n"]?.let { put("thresh", it) }
            v["ratio_$n"]?.let { put("ratio", it) }
            v["gain_$n"]?.let { put("gain", it) }
        }
    )
}

/**
 * A preset this account can see: its own, one shared with it, or a
 * public one - see the server's PresetStore. owner is null for presets
 * saved before owners existed. sharedWith is only ever filled in on the
 * account's own presets; who else has someone else's is not its
 * business. An id of "" means a server from before owners, which lists
 * and loads by name alone.
 */
data class Preset(
    val id: String,
    val name: String,
    val owner: String?,
    val mine: Boolean,
    val public: Boolean,
    val sharedWithMe: Boolean,
    val sharedWith: List<String>,
    // The aux it was saved from, and only loads onto - null for a preset
    // from before auxes were recorded, which loads onto any.
    val aux: Int? = null,
    val auxName: String? = null,
    // First and last name where the account has one, else null/empty -
    // older servers send neither, and usernames are shown instead.
    val ownerName: String? = null,
    val sharedWithNames: List<String> = emptyList()
) {
    /** The line under the name in the Load sheet. */
    val subtitle: String
        get() = when {
            mine && public -> "Yours · Public"
            mine && sharedWith.isNotEmpty() ->
                "Yours · Shared with ${
                    (if (sharedWithNames.size == sharedWith.size) sharedWithNames else sharedWith)
                        .joinToString(", ")
                }"
            mine -> "Yours · Private"
            // Someone else's, shared with this account or public: who it
            // belongs to is what matters.
            owner != null -> "Owner: ${ownerName ?: owner}"
            else -> "Public"
        } + " · " + (if (aux == null) "Any aux" else auxName ?: "Aux $aux")
}
