package com.clmix

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.pow

/**
 * A channel's EQ curve in miniature: the strip's EQ button.
 *
 * The same response the EQ screen draws (EqResponse), squeezed into the
 * width of a strip - no grid labels and no handles, just the 0 dB line
 * and the curve, so a row of strips shows at a glance which channels are
 * cut, boosted or filtered. Drawn in the accent colour while the EQ is in
 * and greyed while it is out, the way the full graph does. Before the
 * console has reported anything it is a flat line.
 *
 * Clickable like any button; the ripple comes from the theme's
 * selectableItemBackground laid over the rounded panel.
 */
class EqThumbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    var eq: ChannelEq? = null
        set(value) {
            if (field == value) return
            field = value
            invalidate()
        }

    private val density = resources.displayMetrics.density
    private val bounds = RectF()
    private val path = Path()
    private val fillPath = Path()

    private val panelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.mute_inactive)
    }

    private val zeroPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1f * density
        color = withAlpha(ContextCompat.getColor(context, R.color.on_surface_variant), 90)
    }

    private val curvePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.8f * density
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
    }

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }

    private val primary = ContextCompat.getColor(context, R.color.primary)
    private val muted = ContextCompat.getColor(context, R.color.on_surface_variant)

    init {
        isClickable = true
        isFocusable = true

        val ripple = context.obtainStyledAttributes(
            intArrayOf(android.R.attr.selectableItemBackground)
        )
        foreground = ripple.getDrawable(0)
        ripple.recycle()
    }

    override fun onDraw(canvas: Canvas) {
        val radius = CORNER_DP * density
        bounds.set(0f, 0f, width.toFloat(), height.toFloat())
        canvas.drawRoundRect(bounds, radius, radius, panelPaint)

        val inset = INSET_DP * density
        val left = inset
        val right = width - inset
        val top = inset
        val bottom = height - inset
        val zeroY = (top + bottom) / 2

        canvas.drawLine(left, zeroY, right, zeroY, zeroPaint)

        val eq = eq
        val eqOut = eq?.eqIn == false
        val color = if (eq == null || eqOut) muted else primary

        path.reset()
        val steps = ((right - left) / density).toInt().coerceAtLeast(24)

        for (i in 0..steps) {
            val x = left + (right - left) * i / steps
            val hz = MIN_HZ * (MAX_HZ / MIN_HZ).pow(i.toDouble() / steps)

            // EQ out: the shape it would have, greyed - as on the screen.
            val db = when {
                eq == null -> 0.0
                eqOut -> EqResponse.totalDbAsIfIn(eq, hz)
                else -> EqResponse.totalDb(eq, hz)
            }.coerceIn(-DB_SPAN, DB_SPAN)

            val y = zeroY - (db / DB_SPAN * (zeroY - top)).toFloat()
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }

        fillPath.set(path)
        fillPath.lineTo(right, zeroY)
        fillPath.lineTo(left, zeroY)
        fillPath.close()
        fillPaint.color = withAlpha(color, if (eqOut) 30 else 60)
        canvas.drawPath(fillPath, fillPaint)

        curvePaint.color = withAlpha(color, if (eqOut) 160 else 255)
        canvas.drawPath(path, curvePaint)
    }

    companion object {
        private const val MIN_HZ = 20.0
        private const val MAX_HZ = 20000.0

        // Tighter than the full graph's ±20: in 40dp of height a few dB
        // of boost has to read as a shape rather than a ripple. A bigger
        // move, or a filter's skirt, simply runs into the edge.
        private const val DB_SPAN = 12.0

        private const val CORNER_DP = 10f
        private const val INSET_DP = 4f

        private fun withAlpha(color: Int, alpha: Int): Int =
            Color.argb(alpha, Color.red(color), Color.green(color), Color.blue(color))

    }
}
