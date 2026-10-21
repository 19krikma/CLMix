package com.clmix

import kotlin.math.hypot
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.sqrt

/**
 * The shape the EQ screen draws: what the channel's filters and bands do
 * to each frequency, in dB.
 *
 * A picture of the EQ, not a model of the console's DSP. Each band is the
 * textbook analogue prototype (RBJ's peaking and shelving forms) evaluated
 * straight at the frequency, and each filter a 12 dB/octave Butterworth -
 * close enough to read where a cut sits and how wide it is, which is what
 * the graph is for. The desk's own filter slopes and Q law have never been
 * measured, so the drawn curve should not be read to a fraction of a dB.
 */
object EqResponse {

    enum class Shape { BELL, LOW_SHELF, HIGH_SHELF }

    // Console band numbers by role. On every channel ever captured band 1
    // sat highest and band 4 lowest, and a freshly reset bus seeds them
    // that way round too (PROTOCOL.md's input and aux EQ tables), so the
    // labels follow it. Only the labels and the shelf choice below lean on
    // this - the graph places every band by its own frequency.
    const val HIGH_BAND = 1
    const val LOW_BAND = EQ_BAND_COUNT

    /** Low to high, the order the screen lays the bands out in. */
    val bandsLowToHigh: List<Int> = (EQ_BAND_COUNT downTo 1).toList()

    fun label(band: Int): String = when (band) {
        4 -> "LF"
        3 -> "LMF"
        2 -> "HMF"
        1 -> "HF"
        else -> "B$band"
    }

    /**
     * eq_curve is an enum over 1..4 and only 1 has ever been seen on a
     * desk, so 1 is drawn as the bell it plainly is. Anything else on an
     * outer band is drawn as that band's shelf - the alternative curve an
     * outer band on a console almost always has - and as a bell on a mid
     * band. A guess about the picture only: the value is never written.
     */
    fun shape(band: EqBand): Shape {
        if (band.curve == null || band.curve == 1) return Shape.BELL

        return when (band.band) {
            LOW_BAND -> Shape.LOW_SHELF
            HIGH_BAND -> Shape.HIGH_SHELF
            else -> Shape.BELL
        }
    }

    /** One band's own contribution at [hz], ignoring whether it is on. */
    fun bandDb(band: EqBand, hz: Double): Double {
        val f0 = band.freq ?: return 0.0
        val gain = band.gain ?: return 0.0
        if (gain == 0.0) return 0.0

        val q = (band.q ?: DEFAULT_Q).coerceIn(EQ_Q_RANGE)
        val a = 10.0.pow(gain / 40.0)
        val w = hz / f0
        val w2 = w * w

        val magnitude = when (shape(band)) {
            Shape.BELL -> {
                val num = hypot(1 - w2, w * a / q)
                val den = hypot(1 - w2, w / (a * q))
                num / den
            }

            // Shelves use a fixed slope rather than the band's Q: a shelf
            // drawn with a bell's narrow Q overshoots into a bump at the
            // corner that nothing on the desk would produce.
            Shape.LOW_SHELF -> {
                val k = sqrt(a) / SHELF_Q * w
                a * hypot(a - w2, k) / hypot(1 - a * w2, k)
            }

            Shape.HIGH_SHELF -> {
                val k = sqrt(a) / SHELF_Q * w
                a * hypot(1 - a * w2, k) / hypot(a - w2, k)
            }
        }

        return 20 * log10(magnitude)
    }

    /** A 12 dB/octave high-pass (lo = true) or low-pass at [hz]. */
    fun filterDb(filter: EqFilter, lo: Boolean, hz: Double): Double {
        if (filter.isIn != true) return 0.0
        val fc = filter.freq ?: return 0.0

        val w = hz / fc
        val den = hypot(1 - w * w, w / BUTTERWORTH_Q)
        val magnitude = if (lo) (w * w) / den else 1 / den

        return 20 * log10(magnitude.coerceAtLeast(MIN_MAGNITUDE))
    }

    /**
     * The whole channel at [hz]: the filters, plus every band that is on
     * - provided the EQ itself is in. The filters sit outside the EQ's
     * own in/out on the desk, so switching the EQ out leaves them drawn.
     */
    fun totalDb(eq: ChannelEq, hz: Double): Double {
        var db = filterDb(eq.loFilter, lo = true, hz) + filterDb(eq.hiFilter, lo = false, hz)

        if (eq.eqIn != false) {
            for (band in eq.bands) {
                if (band.on != false) db += bandDb(band, hz)
            }
        }

        return db
    }

    /** What the curve would be with the EQ switched in - drawn, greyed,
     *  while it is out, so the shape is still readable. */
    fun totalDbAsIfIn(eq: ChannelEq, hz: Double): Double =
        totalDb(eq.copy(eqIn = true), hz)

    // What a band with no Q reported yet is drawn with.
    private const val DEFAULT_Q = 0.71

    private const val SHELF_Q = 0.71
    private const val BUTTERWORTH_Q = 0.7071

    // -200 dB: far below anything the graph shows, and keeps log10 finite
    // at the very bottom of a filter's skirt.
    private const val MIN_MAGNITUDE = 1e-10
}

/** "45 Hz", "125 Hz", "1.25k", "12.5k" - the way a console prints them. */
fun formatEqFreq(hz: Double?): String {
    if (hz == null) return "--"

    return when {
        hz < 100 -> String.format("%.1f Hz", hz)
        hz < 1000 -> String.format("%.0f Hz", hz)
        hz < 10000 -> String.format("%.2fk", hz / 1000)
        else -> String.format("%.1fk", hz / 1000)
    }
}

fun formatEqGain(db: Double?): String {
    if (db == null) return "--"
    val rounded = Math.round(db * 10) / 10.0
    return if (rounded > 0) String.format("+%.1f dB", rounded) else String.format("%.1f dB", rounded)
}

fun formatEqQ(q: Double?): String = if (q == null) "--" else String.format("%.2f", q)
