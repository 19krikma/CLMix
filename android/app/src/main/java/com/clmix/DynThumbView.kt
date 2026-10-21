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

/**
 * One dynamics section's curve in miniature: a strip's COMP or GATE
 * button.
 *
 * Level in across, level out up, -60..0 dB both ways - the Dynamics
 * screen's graph (DynGraphView) for just this one section, with no grid,
 * labels or handles. The compressor reads as a line bending over above
 * its threshold, the gate as a line dropping away below its own, so the
 * two stacked buttons tell themselves apart without a word on either.
 * Drawn in the accent colour while the section is in and greyed while it
 * is out, the way the EQ thumbnail above them is (EqThumbView). Before
 * the console has reported anything it is the plain diagonal.
 */
class DynThumbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    /** True to draw the compressor's curve, false the gate's. */
    var comp: Boolean = true
        set(value) {
            if (field == value) return
            field = value
            invalidate()
        }

    var section: DynSection? = null
        set(value) {
            if (field == value) return
            field = value
            invalidate()
        }

    private val density = resources.displayMetrics.density
    private val bounds = RectF()
    private val curve = Path()
    private val fillPath = Path()

    private val panelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.mute_inactive)
    }

    private val unityPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1f * density
        color = withAlpha(ContextCompat.getColor(context, R.color.on_surface_variant), 70)
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

        fun x(db: Double) = left + ((db - MIN_DB) / -MIN_DB * (right - left)).toFloat()
        fun y(db: Double) =
            bottom - ((db.coerceIn(MIN_DB, 0.0) - MIN_DB) / -MIN_DB * (bottom - top)).toFloat()

        canvas.drawLine(left, bottom, right, top, unityPaint)

        val section = section
        val isIn = section?.isIn == true

        // Out, the curve the section would make is still drawn, greyed -
        // the same reading as the EQ thumbnail. Unreported, the diagonal.
        val shaped = section?.copy(isIn = true)
        val color = if (isIn) primary else muted

        curve.reset()
        val steps = ((right - left) / density).toInt().coerceAtLeast(24)

        for (i in 0..steps) {
            val input = MIN_DB + -MIN_DB * i / steps
            val output = when {
                shaped == null -> input
                comp -> DynResponse.comp(shaped, input)
                else -> DynResponse.gate(shaped, input)
            }
            val px = x(input)
            val py = y(output)
            if (i == 0) curve.moveTo(px, py) else curve.lineTo(px, py)
        }

        fillPath.set(curve)
        fillPath.lineTo(right, bottom)
        fillPath.lineTo(left, bottom)
        fillPath.close()
        fillPaint.color = withAlpha(color, if (isIn) 50 else 25)

        canvas.save()
        canvas.clipRect(left, top, right, bottom)
        canvas.drawPath(fillPath, fillPaint)
        curvePaint.color = withAlpha(color, if (isIn) 255 else 150)
        canvas.drawPath(curve, curvePaint)
        canvas.restore()
    }

    companion object {
        private const val MIN_DB = -60.0
        private const val CORNER_DP = 8f
        private const val INSET_DP = 4f

        private fun withAlpha(color: Int, alpha: Int): Int =
            Color.argb(alpha, Color.red(color), Color.green(color), Color.blue(color))
    }
}
