package com.clmix

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Post-fader meter drawn beside a channel's fader, mirroring the desktop
 * app's AuxLevelsPanel meter: one bar for a mono channel, two narrow bars
 * sharing the same total width for a stereo one, so a strip never changes
 * width depending on the console's configuration.
 *
 * The scale is the console's own 0..-60 dB, NOT the fader's -150..+10 -
 * the two are not interchangeable and this is deliberately not drawn
 * against the fader's ruler.
 *
 * Ballistics match the desktop's: the bar is always falling, and each
 * arriving sample pushes it back up. A sample is a one-shot push rather
 * than a level to settle on, which is why setLevels() takes a sequence
 * that only advances when the server actually sent something new -
 * re-applying the same latched value every frame would hold the bar up
 * forever.
 */
class ChannelMeterView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    private class Leg {
        var shown = FLOOR_DB
        var target = FLOOR_DB
        var rising = false
        var peakHold = FLOOR_DB
        var peakHeldAt = 0L
        var seq = -1L
    }

    private val legs = listOf(Leg(), Leg())

    private var stereo = false
    private var lastFrameAt = 0L

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val peakPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = PEAK_COLOR
    }

    /** True once anything is moving, so idle strips stop invalidating. */
    private var animating = false

    fun setStereo(value: Boolean) {
        if (stereo != value) {
            stereo = value
            invalidate()
        }
    }

    /**
     * peak/rms in dB below zero (negative), or null for no signal.
     * [sequence] must advance only when the server sent a fresh frame.
     */
    fun setLevels(
        sequence: Long,
        leftPeak: Double?, leftRms: Double?,
        rightPeak: Double?, rightRms: Double?
    ) {
        applyLeg(legs[0], sequence, leftPeak, leftRms)
        applyLeg(legs[1], sequence, rightPeak, rightRms)

        if (!animating) {
            animating = true
            lastFrameAt = System.nanoTime()
            postInvalidateOnAnimation()
        }
    }

    fun reset() {
        for (leg in legs) {
            leg.shown = FLOOR_DB
            leg.target = FLOOR_DB
            leg.rising = false
            leg.peakHold = FLOOR_DB
            leg.seq = -1L
        }
        invalidate()
    }

    private fun applyLeg(leg: Leg, sequence: Long, peak: Double?, rms: Double?) {
        if (leg.seq == sequence) return
        leg.seq = sequence

        // The console reports peak and RMS independently and either may
        // be at its floor sentinel, so drive the bar from whichever is
        // actually present.
        val target = max(rms ?: FLOOR_DB, peak ?: FLOOR_DB)
        leg.target = target
        leg.rising = target > leg.shown
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val now = System.nanoTime()
        val elapsed = if (lastFrameAt == 0L) 0.0
                      else min(0.25, (now - lastFrameAt) / 1_000_000_000.0)
        lastFrameAt = now

        val active = if (stereo) 2 else 1
        val gap = if (stereo) GAP_DP * resources.displayMetrics.density else 0f
        val barWidth = (width - gap * (active - 1)) / active

        var moving = false

        for (index in 0 until active) {
            val leg = legs[index]
            advance(leg, elapsed, now / 1_000_000)

            if (leg.shown > FLOOR_DB || leg.peakHold > FLOOR_DB) moving = true

            val x0 = index * (barWidth + gap)
            drawLeg(canvas, leg, x0, barWidth)
        }

        animating = moving
        if (moving) postInvalidateOnAnimation()
    }

    private fun advance(leg: Leg, elapsed: Double, nowMs: Long) {
        if (leg.rising) {
            // Ease up to the sample, never overshooting it. Once reached
            // the push is spent and the release takes over again.
            val decay = exp(-elapsed / RISE_TAU_SECONDS)
            val eased = leg.target + (leg.shown - leg.target) * decay
            leg.shown = min(leg.target, max(eased, leg.shown + RISE_MIN_DB_PER_SEC * elapsed))
            if (leg.shown >= leg.target) leg.rising = false
        } else {
            leg.shown = max(FLOOR_DB, leg.shown - RELEASE_DB_PER_SEC * elapsed)
        }

        // Tracked against the incoming sample rather than the animated
        // bar, so a brief transient still marks its true peak while the
        // bar is still sliding up to it.
        if (leg.target >= leg.peakHold) {
            leg.peakHold = leg.target
            leg.peakHeldAt = nowMs
        } else if (nowMs - leg.peakHeldAt > PEAK_HOLD_MS) {
            leg.peakHold = max(leg.shown, leg.peakHold - PEAK_FALL_DB_PER_SEC * elapsed)
        }
    }

    private fun drawLeg(canvas: Canvas, leg: Leg, x0: Float, barWidth: Float) {
        val h = height.toFloat()
        val slices = (h / sliceHeight()).roundToInt().coerceAtLeast(1)
        val lit = (fraction(leg.shown) * slices).roundToInt()

        for (i in 0 until slices) {
            val db = FLOOR_DB + (i + 0.5) / slices * -FLOOR_DB
            val rgb = gradientAt(db)
            barPaint.color = if (i < lit) rgb else dim(rgb)

            val bottom = h - i * (h / slices)
            canvas.drawRect(x0, bottom - (h / slices), x0 + barWidth, bottom, barPaint)
        }

        if (leg.peakHold > FLOOR_DB) {
            val y = h - fraction(leg.peakHold).toFloat() * h
            canvas.drawRect(x0, y - peakThickness(), x0 + barWidth, y, peakPaint)
        }
    }

    private fun sliceHeight() = SLICE_DP * resources.displayMetrics.density
    private fun peakThickness() = max(1f, resources.displayMetrics.density)

    private fun fraction(db: Double): Double =
        min(1.0, max(0.0, (db - FLOOR_DB) / -FLOOR_DB))

    /** Linear interpolation between the stops bracketing this dB. */
    private fun gradientAt(db: Double): Int {
        var low = GRADIENT.first()
        var high = GRADIENT.last()

        for (i in 0 until GRADIENT.size - 1) {
            if (db >= GRADIENT[i].first && db <= GRADIENT[i + 1].first) {
                low = GRADIENT[i]
                high = GRADIENT[i + 1]
                break
            }
        }

        val span = high.first - low.first
        val ratio = if (span == 0.0) 0.0 else (db - low.first) / span
        val r = channelOf(low.second, 16) + (channelOf(high.second, 16) - channelOf(low.second, 16)) * ratio
        val g = channelOf(low.second, 8) + (channelOf(high.second, 8) - channelOf(low.second, 8)) * ratio
        val b = channelOf(low.second, 0) + (channelOf(high.second, 0) - channelOf(low.second, 0)) * ratio

        return (0xFF shl 24) or (r.toInt() shl 16) or (g.toInt() shl 8) or b.toInt()
    }

    private fun channelOf(color: Int, shift: Int): Double =
        ((color shr shift) and 0xFF).toDouble()

    /** Unlit slices keep their hue at low brightness, as on the desk. */
    private fun dim(color: Int): Int {
        val r = (((color shr 16) and 0xFF) * DIM_FACTOR).toInt()
        val g = (((color shr 8) and 0xFF) * DIM_FACTOR).toInt()
        val b = ((color and 0xFF) * DIM_FACTOR).toInt()
        return (0xFF shl 24) or (r shl 16) or (g shl 8) or b
    }

    companion object {
        const val FLOOR_DB = -60.0

        private const val GAP_DP = 1.5f
        private const val SLICE_DP = 1.5f
        private const val DIM_FACTOR = 0.22
        private const val PEAK_COLOR = 0xFFE6EDF3.toInt()

        private const val RISE_TAU_SECONDS = 0.045
        private const val RISE_MIN_DB_PER_SEC = 30.0
        private const val RELEASE_DB_PER_SEC = 40.0
        private const val PEAK_HOLD_MS = 900L
        private const val PEAK_FALL_DB_PER_SEC = 40.0

        // Sampled from the SD7's own meters, same stops as the desktop.
        private val GRADIENT = listOf(
            -60.0 to 0x0A30C8,
            -46.0 to 0x00A0E8,
            -34.0 to 0x00D24A,
            -20.0 to 0x7ADA00,
            -14.0 to 0xD8D000,
            -10.0 to 0xE8401C,
            0.0 to 0xFF1A10
        )
    }
}
