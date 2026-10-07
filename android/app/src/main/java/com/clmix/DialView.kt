package com.clmix

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.min
import kotlin.math.sin

/**
 * A round dial that is turned by holding it and dragging.
 *
 * Deliberately not a slider: gain and trim are set in small deliberate
 * steps on a console, and a strip on a phone screen is far too short to
 * put 60 dB on. So the value follows the finger's *movement* - dragging
 * right turns it up and left turns it down, dragging up or down does the
 * same at half the rate for finer work, and a finger held still leaves it
 * exactly where it is. How far each bit of movement turns it scales with
 * how fast the finger is going: a slow drag creeps for fine work, a quick
 * swipe covers the range. Nothing moves until a touch lands on the dial
 * and everything stops the moment it lifts, which is what keeps a pocket
 * or a stray brush from moving a head amp.
 *
 * The ring around the dial fills with the value's position between [min]
 * and [max], and the pointer turns with it, so where the value sits is
 * readable at a glance without reading the number.
 */
class DialView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    /** Called as the value turns, with the live value. */
    var onValueChanged: ((Double) -> Unit)? = null

    /** Called once the finger lifts, with the value it settled on. */
    var onTurnFinished: ((Double) -> Unit)? = null

    var min: Double = 0.0
    var max: Double = 60.0

    var value: Double = 0.0
        set(newValue) {
            val clamped = newValue.coerceIn(min, max)
            if (field == clamped) return
            field = clamped
            invalidate()
        }

    /** Greyed out until the console has reported a value for this channel. */
    var hasValue: Boolean = true
        set(newValue) {
            if (field == newValue) return
            field = newValue
            invalidate()
        }

    private var engaged = false

    // Past the slop yet - until then a resting thumb's wobble is ignored.
    private var dragging = false
    private var touchStartX = 0f
    private var touchStartY = 0f
    private var lastX = 0f
    private var lastY = 0f
    private var lastEventAt = 0L

    // Finger speed in dp/s, smoothed: per-event speeds jitter a lot at
    // 120 Hz, and that jitter would otherwise show as a lumpy turn.
    private var speed = 0.0

    private val density = resources.displayMetrics.density

    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        color = ContextCompat.getColor(context, R.color.track_background)
    }

    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        color = ContextCompat.getColor(context, R.color.primary)
    }

    private val knobPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = ContextCompat.getColor(context, R.color.mute_inactive)
    }

    private val pointerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        color = ContextCompat.getColor(context, R.color.on_mute_inactive)
    }

    private val arcBounds = RectF()

    /** Turns the dial by one step of finger movement. */
    private fun moveTo(x: Float, y: Float, eventTime: Long) {
        val dx = (x - lastX) / density
        val dy = (y - lastY) / density
        val millis = (eventTime - lastEventAt).coerceAtLeast(1L)

        lastX = x
        lastY = y
        lastEventAt = eventTime

        val instant = hypot(dx, dy) * 1000.0 / millis
        speed += (instant - speed) * SPEED_SMOOTHING

        val unitsPerDp = min(MAX_UNITS_PER_DP, BASE_UNITS_PER_DP + ACCEL_UNITS_PER_DP * speed)

        // Screen y grows downward, so up (negative dy) turns the value up.
        val travel = dx - dy * VERTICAL_RATE

        val next = value + travel * unitsPerDp
        if (next.coerceIn(min, max) != value) {
            value = next
            onValueChanged?.invoke(value)
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val size = (DEFAULT_SIZE_DP * density).toInt()
        setMeasuredDimension(
            resolveSize(size, widthMeasureSpec),
            resolveSize(size, heightMeasureSpec)
        )
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                // Disabled: the ALT column's dials with no alt route.
                if (!hasValue || !isEnabled) return false

                engaged = true
                dragging = false
                touchStartX = event.x
                touchStartY = event.y
                speed = 0.0

                // The sheet this lives in scrolls, and a drag starting on
                // a dial is meant for the dial.
                parent?.requestDisallowInterceptTouchEvent(true)

                invalidate()
                return true
            }

            MotionEvent.ACTION_MOVE -> {
                if (!engaged) return true

                // Batched samples first, so a fast swipe is turned by the
                // path it actually took and its speed read off each step.
                for (i in 0 until event.historySize) {
                    feed(
                        event.getHistoricalX(i),
                        event.getHistoricalY(i),
                        event.getHistoricalEventTime(i)
                    )
                }
                feed(event.x, event.y, event.eventTime)
                return true
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (engaged) {
                    engaged = false
                    parent?.requestDisallowInterceptTouchEvent(false)
                    invalidate()
                    onTurnFinished?.invoke(value)
                }
                return true
            }
        }

        return super.onTouchEvent(event)
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        engaged = false
    }

    private fun feed(x: Float, y: Float, eventTime: Long) {
        if (!dragging) {
            val fromStart = hypot(x - touchStartX, y - touchStartY) / density
            if (fromStart < SLOP_DP) return

            // Counted from here, not from touch-down, so crossing the
            // slop doesn't land as one jump.
            dragging = true
            lastX = x
            lastY = y
            lastEventAt = eventTime
            return
        }

        moveTo(x, y, eventTime)
    }

    override fun onDraw(canvas: Canvas) {
        val centerX = width / 2f
        val centerY = height / 2f
        val ringWidth = RING_WIDTH_DP * density
        val radius = min(centerX, centerY) - ringWidth / 2f

        trackPaint.strokeWidth = ringWidth
        fillPaint.strokeWidth = ringWidth
        pointerPaint.strokeWidth = POINTER_WIDTH_DP * density

        arcBounds.set(
            centerX - radius, centerY - radius,
            centerX + radius, centerY + radius
        )

        canvas.drawArc(arcBounds, SWEEP_START, SWEEP_DEGREES, false, trackPaint)

        val fraction = if (max > min) {
            ((value - min) / (max - min)).toFloat().coerceIn(0f, 1f)
        } else {
            0f
        }

        // Engaged, the fill and knob brighten - the one thing that says
        // the dial is live and about to move something on the console.
        fillPaint.alpha = if (hasValue) 255 else 60
        knobPaint.alpha = if (engaged) 255 else 200

        if (hasValue) {
            canvas.drawArc(
                arcBounds, SWEEP_START, SWEEP_DEGREES * fraction, false, fillPaint
            )
        }

        val knobRadius = radius - ringWidth * 1.4f
        canvas.drawCircle(centerX, centerY, knobRadius, knobPaint)

        if (hasValue) {
            // The pointer turns with the value: this is the part that
            // reads as the dial spinning while it is held.
            val angle = Math.toRadians((SWEEP_START + SWEEP_DEGREES * fraction).toDouble())
            val inner = knobRadius * 0.35f
            canvas.drawLine(
                centerX + (cos(angle) * inner).toFloat(),
                centerY + (sin(angle) * inner).toFloat(),
                centerX + (cos(angle) * knobRadius * 0.8).toFloat(),
                centerY + (sin(angle) * knobRadius * 0.8).toFloat(),
                pointerPaint
            )
        }
    }

    companion object {
        // Opens at the lower left and sweeps to the lower right, the way
        // a console's own rotary markings run - never a full circle, so
        // the ends of the range are visible rather than meeting.
        private const val SWEEP_START = 135f
        private const val SWEEP_DEGREES = 270f

        private const val DEFAULT_SIZE_DP = 64f
        private const val RING_WIDTH_DP = 5f
        private const val POINTER_WIDTH_DP = 2.5f

        // How far the finger travels before anything moves, so resting a
        // thumb on the dial doesn't drift it.
        private const val SLOP_DP = 6f

        // Per dp of finger travel: BASE when crawling, rising with speed
        // to MAX, so ~20dp is 1 dB done slowly and a quick swipe across
        // the sheet covers the whole 60 dB.
        private const val BASE_UNITS_PER_DP = 0.05
        private const val ACCEL_UNITS_PER_DP = 0.00025
        private const val MAX_UNITS_PER_DP = 0.5

        // Up/down turns at this fraction of the sideways rate.
        private const val VERTICAL_RATE = 0.5f

        private const val SPEED_SMOOTHING = 0.3
    }
}
