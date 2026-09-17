package com.clmix

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * A round dial that is turned by holding it and sliding sideways.
 *
 * Deliberately not a slider: gain and trim are set in small deliberate
 * steps on a console, and a strip on a phone screen is far too short to
 * put 60 dB on. So the finger's distance from where it landed sets a
 * *rate* rather than a position - hold a little to the right and the
 * value climbs slowly, push further and it spins up, and the same to the
 * left brings it down. Nothing moves until a touch lands on the dial and
 * everything stops the moment it lifts, which is what keeps a pocket or a
 * stray brush from moving a head amp.
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
    private var touchStartX = 0f
    private var lastX = 0f
    private var lastTickAt = 0L

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

    // Runs while the dial is held, turning the finger's offset into
    // movement. A tick rather than a per-MotionEvent change because the
    // finger can be held perfectly still and still be asking the dial to
    // keep turning - there are no move events in that case.
    private val ticker = object : Runnable {
        override fun run() {
            if (!engaged) return

            val now = SystemClock.uptimeMillis()
            val seconds = (now - lastTickAt) / 1000.0
            lastTickAt = now

            val offset = lastX - touchStartX
            val deadZone = DEAD_ZONE_DP * density

            if (abs(offset) > deadZone) {
                val direction = if (offset > 0) 1.0 else -1.0
                val travel = (abs(offset) - deadZone) / density

                // Squared so a small hold creeps for fine work while a
                // big one spins - linear made the far end unusably slow
                // for 60 dB of range.
                val perSecond = min(
                    MAX_UNITS_PER_SECOND,
                    UNITS_PER_SECOND_AT_1DP * travel * travel
                )

                val next = value + direction * perSecond * seconds

                if (next != value) {
                    value = next
                    onValueChanged?.invoke(value)
                }
            }

            postDelayed(this, TICK_MS)
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
                if (!hasValue) return false

                engaged = true
                touchStartX = event.x
                lastX = event.x
                lastTickAt = SystemClock.uptimeMillis()

                // The sheet this lives in scrolls, and a sideways drag
                // starting on a dial is meant for the dial.
                parent?.requestDisallowInterceptTouchEvent(true)

                invalidate()
                postDelayed(ticker, TICK_MS)
                return true
            }

            MotionEvent.ACTION_MOVE -> {
                lastX = event.x
                return true
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (engaged) {
                    engaged = false
                    removeCallbacks(ticker)
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
        removeCallbacks(ticker)
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
        private const val DEAD_ZONE_DP = 6f

        private const val TICK_MS = 16L

        // At 1dp past the dead zone; squared with distance from there.
        private const val UNITS_PER_SECOND_AT_1DP = 0.06
        private const val MAX_UNITS_PER_SECOND = 40.0
    }
}
