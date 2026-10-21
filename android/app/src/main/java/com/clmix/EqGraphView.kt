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
import android.view.ScaleGestureDetector
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.hypot
import kotlin.math.log10
import kotlin.math.pow

/** What on the EQ screen a control or a graph handle is aimed at. */
sealed class EqTarget {
    data class Band(val band: Int) : EqTarget()

    /** The high-pass, which the console calls lo_filter - it cuts lows. */
    object LoFilter : EqTarget()

    /** The low-pass, the console's hi_filter. */
    object HiFilter : EqTarget()
}

/**
 * The EQ screen's picture of the channel: frequency across (20 Hz..20 kHz,
 * logarithmic), gain up the side, the combined response drawn over a grid,
 * and a handle for every band and filter the console has reported.
 *
 * The handles are the controls. Dragging a band's moves its frequency
 * sideways and its gain up and down; dragging a filter's moves only its
 * frequency. A second finger pinches the selected band's Q - spreading
 * widens it, as on any EQ with a graph. Nothing moves until a touch lands
 * on a handle, so a hand resting on the graph is harmless.
 *
 * This view holds no EQ state of its own beyond what it was last given:
 * every drag is reported through the callbacks, and EqActivity hands back
 * the result through [eq] - so what is drawn is always what the screen
 * believes the desk holds.
 */
class EqGraphView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    var eq: ChannelEq? = null
        set(value) {
            field = value
            invalidate()
        }

    var selected: EqTarget? = null
        set(value) {
            field = value
            invalidate()
        }

    /** A handle was touched. */
    var onSelect: ((EqTarget) -> Unit)? = null

    /** A handle is being dragged: the frequency and (for a band) gain it is at. */
    var onDrag: ((EqTarget, Double, Double?) -> Unit)? = null

    /** A pinch on the selected band, with the Q it asks for. */
    var onPinch: ((Int, Double) -> Unit)? = null

    /** The finger lifted after a drag or pinch. */
    var onGestureEnd: ((EqTarget) -> Unit)? = null

    private val density = resources.displayMetrics.density
    private val plot = RectF()

    private var dragging: EqTarget? = null
    private var grabDx = 0f
    private var grabDy = 0f
    private var pinching = false
    private var pinchQ = 0.0

    private val scaleDetector = ScaleGestureDetector(
        context,
        object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScaleBegin(detector: ScaleGestureDetector): Boolean {
                val band = (selected as? EqTarget.Band)?.band ?: return false
                val q = eq?.bands?.getOrNull(band - 1)?.q ?: return false

                pinching = true
                pinchQ = q
                // The pinch replaces the drag - one finger's wander while
                // the other lands should not drag the band off with it.
                dragging = null
                return true
            }

            override fun onScale(detector: ScaleGestureDetector): Boolean {
                val band = (selected as? EqTarget.Band)?.band ?: return false

                // Spreading the fingers widens the band, which is a lower
                // Q - so Q moves against the span, in proportion to it.
                pinchQ = (pinchQ / detector.scaleFactor).coerceIn(EQ_Q_RANGE)
                onPinch?.invoke(band, pinchQ)
                return true
            }
        }
    )

    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1f * density
        color = withAlpha(ContextCompat.getColor(context, R.color.on_surface_variant), 50)
    }

    private val zeroPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.2f * density
        color = withAlpha(ContextCompat.getColor(context, R.color.on_surface_variant), 120)
    }

    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, R.color.on_surface_variant)
        textSize = 10f * resources.displayMetrics.scaledDensity
    }

    private val curvePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2.5f * density
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
    }

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    private val bandCurvePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 1.5f * density
        pathEffect = DashPathEffect(floatArrayOf(5f * density, 4f * density), 0f)
    }

    private val nodeFill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }

    private val nodeStroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 2f * density
    }

    private val nodeText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        textSize = 9f * resources.displayMetrics.scaledDensity
        isFakeBoldText = true
    }

    private val backgroundPaint = Paint().apply {
        color = ContextCompat.getColor(context, R.color.surface)
    }

    private val curvePath = Path()
    private val fillPath = Path()
    private val bandPath = Path()

    private val primary = ContextCompat.getColor(context, R.color.primary)
    private val muted = ContextCompat.getColor(context, R.color.on_surface_variant)

    // MARK: - Geometry

    private fun xFor(hz: Double): Float {
        val fraction = log10(hz / MIN_HZ) / log10(MAX_HZ / MIN_HZ)
        return plot.left + (fraction * plot.width()).toFloat()
    }

    private fun hzFor(x: Float): Double {
        val fraction = ((x - plot.left) / plot.width()).coerceIn(0f, 1f).toDouble()
        return (MIN_HZ * (MAX_HZ / MIN_HZ).pow(fraction)).coerceIn(EQ_FREQ_RANGE)
    }

    private fun yFor(db: Double): Float {
        val clamped = db.coerceIn(-DB_SPAN, DB_SPAN)
        return plot.top + ((DB_SPAN - clamped) / (2 * DB_SPAN) * plot.height()).toFloat()
    }

    private fun dbFor(y: Float): Double {
        val fraction = ((y - plot.top) / plot.height()).coerceIn(0f, 1f).toDouble()
        return (DB_SPAN - fraction * 2 * DB_SPAN).coerceIn(EQ_GAIN_RANGE)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        plot.set(
            paddingLeft + LEFT_GUTTER_DP * density,
            paddingTop + EDGE_DP * density,
            w - paddingRight - EDGE_DP * density,
            h - paddingBottom - BOTTOM_GUTTER_DP * density
        )
    }

    /** Where a target's handle sits, or null if it has nothing to place it by. */
    private fun handleFor(eq: ChannelEq, target: EqTarget): Pair<Float, Float>? = when (target) {
        is EqTarget.Band -> {
            val band = eq.bands.getOrNull(target.band - 1)
            val freq = band?.freq
            if (freq == null) null else xFor(freq) to yFor(band.gain ?: 0.0)
        }

        // On the curve itself at the corner frequency, where a filter
        // visibly bends it - and on the 0 dB line while it is out.
        EqTarget.LoFilter -> eq.loFilter.freq?.let {
            xFor(it) to yFor(EqResponse.filterDb(eq.loFilter, lo = true, it))
        }

        EqTarget.HiFilter -> eq.hiFilter.freq?.let {
            xFor(it) to yFor(EqResponse.filterDb(eq.hiFilter, lo = false, it))
        }
    }

    private fun allTargets(): List<EqTarget> =
        listOf(EqTarget.LoFilter, EqTarget.HiFilter) +
            EqResponse.bandsLowToHigh.map { EqTarget.Band(it) }

    // MARK: - Drawing

    override fun onDraw(canvas: Canvas) {
        canvas.drawRect(plot, backgroundPaint)
        drawGrid(canvas)

        val eq = eq ?: return
        val eqOut = eq.eqIn == false

        // The selected band on its own, dashed, so its shape reads apart
        // from the sum of the others.
        (selected as? EqTarget.Band)?.let { target ->
            val band = eq.bands.getOrNull(target.band - 1)
            if (band?.freq != null) {
                tracePath(bandPath) { hz -> EqResponse.bandDb(band, hz) }
                bandCurvePaint.color = withAlpha(colorFor(target), 200)
                canvas.drawPath(bandPath, bandCurvePaint)
            }
        }

        // EQ out: the shape it would have is still drawn, greyed, so an
        // engineer can see what switching it back in would do.
        tracePath(curvePath) { hz ->
            if (eqOut) EqResponse.totalDbAsIfIn(eq, hz) else EqResponse.totalDb(eq, hz)
        }

        val lineColor = if (eqOut) muted else primary

        fillPath.set(curvePath)
        fillPath.lineTo(plot.right, yFor(0.0))
        fillPath.lineTo(plot.left, yFor(0.0))
        fillPath.close()
        fillPaint.color = withAlpha(lineColor, if (eqOut) 25 else 45)
        canvas.drawPath(fillPath, fillPaint)

        curvePaint.color = withAlpha(lineColor, if (eqOut) 150 else 255)
        canvas.drawPath(curvePath, curvePaint)

        for (target in allTargets()) drawHandle(canvas, eq, target)
    }

    private fun drawGrid(canvas: Canvas) {
        for (db in DB_LINES) {
            val y = yFor(db)
            canvas.drawLine(plot.left, y, plot.right, y, if (db == 0.0) zeroPaint else gridPaint)

            val label = if (db > 0) "+${db.toInt()}" else db.toInt().toString()
            labelPaint.textAlign = Paint.Align.RIGHT
            canvas.drawText(
                label, plot.left - 5 * density, y + labelPaint.textSize / 3, labelPaint
            )
        }

        labelPaint.textAlign = Paint.Align.CENTER

        for (hz in FREQ_LINES) {
            val x = xFor(hz)
            canvas.drawLine(x, plot.top, x, plot.bottom, gridPaint)
        }

        for ((hz, label) in FREQ_LABELS) {
            canvas.drawText(
                label, xFor(hz), plot.bottom + labelPaint.textSize + 4 * density, labelPaint
            )
        }
    }

    private inline fun tracePath(path: Path, response: (Double) -> Double) {
        path.reset()

        val steps = (plot.width() / (2 * density)).toInt().coerceAtLeast(64)

        for (i in 0..steps) {
            val x = plot.left + plot.width() * i / steps
            val y = yFor(response(hzFor(x)))
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
    }

    private fun drawHandle(canvas: Canvas, eq: ChannelEq, target: EqTarget) {
        val (x, y) = handleFor(eq, target) ?: return

        val active = when (target) {
            is EqTarget.Band -> eq.eqIn != false &&
                eq.bands.getOrNull(target.band - 1)?.on != false
            EqTarget.LoFilter -> eq.loFilter.isIn == true
            EqTarget.HiFilter -> eq.hiFilter.isIn == true
        }

        val color = colorFor(target)
        val isSelected = target == selected
        val radius = (if (isSelected) NODE_SELECTED_DP else NODE_DP) * density

        // Off reads as an outline only: the handle is still there to grab,
        // but nothing it stands for is shaping the sound.
        nodeFill.color = if (active) color else backgroundPaint.color
        nodeStroke.color = if (isSelected) ContextCompat.getColor(context, R.color.on_surface) else color

        if (target is EqTarget.Band) {
            canvas.drawCircle(x, y, radius, nodeFill)
            canvas.drawCircle(x, y, radius, nodeStroke)
        } else {
            canvas.drawRoundRect(
                x - radius, y - radius, x + radius, y + radius,
                3 * density, 3 * density, nodeFill
            )
            canvas.drawRoundRect(
                x - radius, y - radius, x + radius, y + radius,
                3 * density, 3 * density, nodeStroke
            )
        }

        nodeText.color = if (active) Color.WHITE else color
        canvas.drawText(shortLabel(target), x, y + nodeText.textSize / 3, nodeText)
    }

    private fun shortLabel(target: EqTarget): String = when (target) {
        is EqTarget.Band -> when (target.band) {
            4 -> "LF"
            3 -> "LM"
            2 -> "HM"
            1 -> "HF"
            else -> target.band.toString()
        }
        EqTarget.LoFilter -> "HP"
        EqTarget.HiFilter -> "LP"
    }

    fun colorFor(target: EqTarget): Int = eqTargetColor(context, target)

    // MARK: - Touch

    override fun onTouchEvent(event: MotionEvent): Boolean {
        val eq = eq ?: return false

        scaleDetector.onTouchEvent(event)

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                val hit = nearestHandle(eq, event.x, event.y) ?: return true
                val (hx, hy) = handleFor(eq, hit) ?: return true

                // Kept so the handle stays where it was under the finger
                // rather than jumping its centre to the touch point.
                grabDx = hx - event.x
                grabDy = hy - event.y
                dragging = hit
                pinching = false

                parent?.requestDisallowInterceptTouchEvent(true)
                onSelect?.invoke(hit)
                return true
            }

            MotionEvent.ACTION_MOVE -> {
                val target = dragging ?: return true
                if (pinching || event.pointerCount > 1) return true

                val hz = hzFor(event.x + grabDx)
                val db = if (target is EqTarget.Band) dbFor(event.y + grabDy) else null
                onDrag?.invoke(target, hz, db)
                return true
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                val target = dragging ?: (if (pinching) selected else null)
                dragging = null
                pinching = false
                parent?.requestDisallowInterceptTouchEvent(false)
                target?.let { onGestureEnd?.invoke(it) }
                return true
            }
        }

        return true
    }

    private fun nearestHandle(eq: ChannelEq, x: Float, y: Float): EqTarget? {
        var best: EqTarget? = null
        var bestDistance = TOUCH_RADIUS_DP * density

        // The selected handle wins a tie, so a band sitting under another
        // stays reachable by selecting it from the chips first.
        val order = listOfNotNull(selected) + allTargets().filter { it != selected }

        for (target in order) {
            val (hx, hy) = handleFor(eq, target) ?: continue
            val distance = hypot(hx - x, hy - y)

            if (distance < bestDistance) {
                best = target
                bestDistance = distance
            }
        }

        return best
    }

    companion object {
        private const val MIN_HZ = 20.0
        private const val MAX_HZ = 20000.0

        // A little past the bands' ±18, so a band at the limit is still
        // drawn inside the grid rather than on its edge.
        private const val DB_SPAN = 20.0

        private val DB_LINES = listOf(-18.0, -12.0, -6.0, 0.0, 6.0, 12.0, 18.0)

        private val FREQ_LINES = listOf(
            30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0,
            200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0,
            2000.0, 3000.0, 4000.0, 5000.0, 6000.0, 7000.0, 8000.0, 9000.0, 10000.0
        )

        private val FREQ_LABELS = listOf(
            50.0 to "50", 100.0 to "100", 200.0 to "200", 500.0 to "500",
            1000.0 to "1k", 2000.0 to "2k", 5000.0 to "5k", 10000.0 to "10k"
        )

        private const val LEFT_GUTTER_DP = 30f
        private const val BOTTOM_GUTTER_DP = 18f
        private const val EDGE_DP = 6f

        private const val NODE_DP = 11f
        private const val NODE_SELECTED_DP = 14f
        private const val TOUCH_RADIUS_DP = 36f

        private fun withAlpha(color: Int, alpha: Int): Int =
            Color.argb(alpha, Color.red(color), Color.green(color), Color.blue(color))
    }
}

/** Each band and filter keeps one colour across the graph and the chips. */
fun eqTargetColor(context: Context, target: EqTarget): Int = ContextCompat.getColor(
    context,
    when (target) {
        is EqTarget.Band -> when (target.band) {
            4 -> R.color.eq_band_lf
            3 -> R.color.eq_band_lmf
            2 -> R.color.eq_band_hmf
            else -> R.color.eq_band_hf
        }
        EqTarget.LoFilter, EqTarget.HiFilter -> R.color.eq_filter
    }
)
