package com.clmix

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat

/**
 * dB scale drawn beside a channel's level fader - numbers plus a short
 * connector line pointing at the fader track, positioned via
 * AuxTaper.LEVEL_TICK_FRACTIONS so a tick's printed position always
 * matches exactly where dragging the fader there reports that dB value.
 * Mirrors the desktop app's AuxLevelsPanel._draw_level_ruler.
 */
class LevelRulerView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : View(context, attrs) {

    private val density = context.resources.displayMetrics.density
    private val inset = INSET_DP * density
    private val lineStart = LINE_START_DP * density

    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.on_surface_variant)
        textSize = 9f * context.resources.displayMetrics.scaledDensity
        // Right-aligned so every label ends at the same place, just
        // before its tick line: left-aligned, a single-character "0" or
        // "5" trailed off toward the strip's edge and left a gap between
        // the number and the fader it labels, while "60" nearly closed
        // it. Hanging them all off the line keeps the numbers up against
        // the fader whatever their width.
        textAlign = Paint.Align.RIGHT
    }

    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.on_surface_variant)
        strokeWidth = density
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val span = height - 2 * inset
        val textBaselineOffset = -(textPaint.descent() + textPaint.ascent()) / 2

        for ((index, tick) in AuxTaper.LEVEL_TICKS.withIndex()) {
            val label = tick.second
            val fraction = AuxTaper.LEVEL_TICK_FRACTIONS[index].toFloat()
            val y = inset + (1 - fraction) * span

            canvas.drawLine(lineStart, y, width.toFloat(), y, linePaint)
            canvas.drawText(
                label, lineStart - LABEL_GAP_DP * density,
                y + textBaselineOffset, textPaint
            )
        }
    }

    companion object {
        // Keeps the top/bottom labels (+10, ∞) from being clipped by the
        // view's edge, since they'd otherwise be vertically centered on
        // the exact top/bottom endpoints.
        private const val INSET_DP = 4f
        // Leaves room for a three-glyph label ("-60") to the left of it.
        private const val LINE_START_DP = 18f

        // Breathing room between a label and the tick line it hangs off.
        private const val LABEL_GAP_DP = 2.5f
    }
}
