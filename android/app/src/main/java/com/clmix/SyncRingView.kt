package com.clmix

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import android.view.animation.LinearInterpolator
import androidx.core.content.ContextCompat

/**
 * The ring on the "Server Syncing" card (see ServerSync): a faint track
 * and a fixed-length arc turning round it at a steady speed.
 *
 * Drawn by hand rather than with Material's CircularProgressIndicator,
 * whose arc grows and shrinks as it goes and whose size includes an
 * inset - in a box sized to the ring that left it clipped and off
 * centre around the arrows, which read as a glitch. This one is always
 * centred in its own bounds, whatever size it is given.
 */
class SyncRingView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null
) : View(context, attrs) {
    private val stroke = 4 * resources.displayMetrics.density

    private val track = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = stroke
        color = ContextCompat.getColor(context, R.color.surface_variant)
    }
    private val arc = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = stroke
        strokeCap = Paint.Cap.ROUND
        color = ContextCompat.getColor(context, R.color.primary)
    }
    private val bounds = RectF()
    private var angle = 0f

    private val spin = ValueAnimator.ofFloat(0f, 360f).apply {
        duration = 1000
        repeatCount = ValueAnimator.INFINITE
        interpolator = LinearInterpolator()
        addUpdateListener {
            angle = it.animatedValue as Float
            invalidate()
        }
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        spin.start()
    }

    override fun onDetachedFromWindow() {
        spin.cancel()
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        val size = minOf(width, height).toFloat()
        val left = (width - size) / 2 + stroke / 2
        val top = (height - size) / 2 + stroke / 2
        bounds.set(left, top, left + size - stroke, top + size - stroke)

        canvas.drawOval(bounds, track)
        canvas.drawArc(bounds, angle - 90f, 100f, false, arc)
    }
}
