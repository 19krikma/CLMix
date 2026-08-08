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
        textAlign = Paint.Align.LEFT
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
            canvas.drawText(label, 2 * density, y + textBaselineOffset, textPaint)
        }
    }

    companion object {
        // Keeps the top/bottom labels (+10, ∞) from being clipped by the
        // view's edge, since they'd otherwise be vertically centered on
        // the exact top/bottom endpoints.
        private const val INSET_DP = 4f
        private const val LINE_START_DP = 16f
    }
}
