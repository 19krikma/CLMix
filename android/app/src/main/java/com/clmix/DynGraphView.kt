package com.clmix

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.abs

/**
 * The Dynamics screen's transfer graph: level in across, level out up the
 * side, both -60..0 dB. The diagonal is "nothing happening"; the curve is
 * what the open section does to it - Dynamics 1 (compSelected) or
 * Dynamics 2 in whichever mode it runs (DynResponse).
 *
 * A static picture, as on the console's own screen: no knee, no attack or
 * release, since those shape time rather than level. The threshold is a
 * handle - drag it sideways to move it.
 */
class DynGraphView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    var dynamics: ChannelDynamics? = null
        set(value) {
            field = value
            invalidate()
        }

    /** True for Dynamics 1, false for Dynamics 2 (the gate slot). */
    var compSelected: Boolean = true
        set(value) {
            field = value
            invalidate()
        }

    /** A threshold handle is being dragged, to this many dB. */
    var onThreshold: ((Boolean, Double) -> Unit)? = null

    var onGestureEnd: (() -> Unit)? = null

    private val density = resources.displayMetrics.density
    private val plot = RectF()
    private var dragging: Boolean? = null
    private var grabDx = 0f

    private val primary = ContextCompat.getColor(context, R.color.primary)
    private val compColor = ContextCompat.getColor(context, R.color.eq_band_lf)
    private val gateColor = ContextCompat.getColor(context, R.color.eq_band_hmf)
    private val muted = ContextCompat.getColor(context, R.color.on_surface_variant)

    private val backgroundPaint = Paint().apply {
        color = ContextCompat.getColor(context, R.color.surface)
    }

    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1f * density
        color = withAlpha(muted, 50)
    }

    private val unityPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.2f * density
        color = withAlpha(muted, 120)
        pathEffect = DashPathEffect(floatArrayOf(5f * density, 4f * density), 0f)
    }

    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = muted
        textSize = 10f * resources.displayMetrics.scaledDensity
    }

    private val curvePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2.5f * density
        strokeJoin = Paint.Join.ROUND
    }

    private val handleFill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }

    private val handleStroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2f * density
    }

    private val handleText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = 9f * resources.displayMetrics.scaledDensity
        isFakeBoldText = true
    }

    private val thresholdPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1f * density
        pathEffect = DashPathEffect(floatArrayOf(3f * density, 3f * density), 0f)
    }

    private val curve = Path()

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)

        // Square, so the unity line runs at 45 degrees and a ratio reads
        // as the angle it is.
        val side = minOf(
            w - (LEFT_GUTTER_DP + EDGE_DP) * density,
            h - (BOTTOM_GUTTER_DP + EDGE_DP) * density
        )
        val left = LEFT_GUTTER_DP * density + ((w - (LEFT_GUTTER_DP + EDGE_DP) * density) - side) / 2
        plot.set(left, EDGE_DP * density, left + side, EDGE_DP * density + side)
    }

    private fun x(db: Double) = plot.left + ((db - MIN_DB) / -MIN_DB * plot.width()).toFloat()
    private fun y(db: Double) =
        plot.bottom - ((db.coerceIn(MIN_DB, 0.0) - MIN_DB) / -MIN_DB * plot.height()).toFloat()
    private fun dbAt(px: Float) =
        (MIN_DB + (px - plot.left) / plot.width() * -MIN_DB).coerceIn(MIN_DB, 0.0)

    /** Level out for level in, through the open section. */
    private fun transfer(dyn: ChannelDynamics, input: Double): Double =
        if (compSelected) DynResponse.comp(dyn.comp, input) else DynResponse.gate(dyn.gate, input)

    override fun onDraw(canvas: Canvas) {
        canvas.drawRect(plot, backgroundPaint)

        labelPaint.textAlign = Paint.Align.RIGHT
        for (db in GRID) {
            canvas.drawLine(plot.left, y(db), plot.right, y(db), gridPaint)
            canvas.drawLine(x(db), plot.top, x(db), plot.bottom, gridPaint)
            canvas.drawText(
                db.toInt().toString(), plot.left - 5 * density,
                y(db) + labelPaint.textSize / 3, labelPaint
            )
        }

        labelPaint.textAlign = Paint.Align.CENTER
        for (db in GRID) {
            canvas.drawText(
                db.toInt().toString(), x(db),
                plot.bottom + labelPaint.textSize + 4 * density, labelPaint
            )
        }

        canvas.drawLine(plot.left, plot.bottom, plot.right, plot.top, unityPaint)

        val dyn = dynamics ?: return

        curve.reset()
        val steps = (plot.width() / (2 * density)).toInt().coerceAtLeast(60)
        for (i in 0..steps) {
            val input = MIN_DB + -MIN_DB * i / steps
            val px = x(input)
            val py = y(transfer(dyn, input))
            if (i == 0) curve.moveTo(px, py) else curve.lineTo(px, py)
        }

        val isIn = (if (compSelected) dyn.comp else dyn.gate).isIn == true
        curvePaint.color = if (isIn) primary else withAlpha(muted, 160)

        canvas.save()
        canvas.clipRect(plot)
        canvas.drawPath(curve, curvePaint)
        canvas.restore()

        drawHandle(canvas, dyn, comp = compSelected)
    }

    private fun handleX(dyn: ChannelDynamics, comp: Boolean): Float? {
        val section = if (comp) dyn.comp else dyn.gate
        return section.values["thresh"]?.let { x(it) }
    }

    private fun drawHandle(canvas: Canvas, dyn: ChannelDynamics, comp: Boolean) {
        val section = if (comp) dyn.comp else dyn.gate
        val thresh = section.values["thresh"] ?: return
        val hx = x(thresh)
        val color = if (comp) compColor else gateColor
        val active = section.isIn == true

        // The threshold as a line down the whole graph, so where the
        // section starts acting reads against both axes.
        thresholdPaint.color = withAlpha(color, 200)
        canvas.drawLine(hx, plot.top, hx, plot.bottom, thresholdPaint)

        val hy = y(transfer(dyn, thresh)).coerceIn(plot.top, plot.bottom)
        val radius = 14f * density

        handleFill.color = if (active) color else backgroundPaint.color
        handleStroke.color = ContextCompat.getColor(context, R.color.on_surface)
        canvas.drawCircle(hx, hy, radius, handleFill)
        canvas.drawCircle(hx, hy, radius, handleStroke)

        handleText.color = if (active) Color.WHITE else color
        val label = if (comp) "C" else (GateMode.of(section.mode) ?: GateMode.GATE).handle
        canvas.drawText(label, hx, hy + handleText.textSize / 3, handleText)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        val dyn = dynamics ?: return false

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                // The threshold sits on a vertical line, so a touch
                // anywhere up the line near it grabs it.
                val hit = compSelected
                val hx = handleX(dyn, hit) ?: return true
                if (abs(hx - event.x) >= TOUCH_DP * density) return true

                grabDx = hx - event.x
                dragging = hit
                parent?.requestDisallowInterceptTouchEvent(true)
            }

            MotionEvent.ACTION_MOVE -> {
                val comp = dragging ?: return true
                onThreshold?.invoke(comp, dbAt(event.x + grabDx))
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (dragging != null) onGestureEnd?.invoke()
                dragging = null
                parent?.requestDisallowInterceptTouchEvent(false)
            }
        }

        return true
    }

    companion object {
        private const val MIN_DB = -60.0
        private val GRID = listOf(-60.0, -48.0, -36.0, -24.0, -12.0, 0.0)

        private const val LEFT_GUTTER_DP = 30f
        private const val BOTTOM_GUTTER_DP = 18f
        private const val EDGE_DP = 6f
        private const val TOUCH_DP = 24f

        private fun withAlpha(color: Int, alpha: Int): Int =
            Color.argb(alpha, Color.red(color), Color.green(color), Color.blue(color))
    }
}
