package com.clmix

import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable

/**
 * Pan is a balance value, not a fill-from-zero quantity - a stock
 * SeekBar progressDrawable fills from the left edge, which reads as
 * "how much" rather than "which way." This fills from the track's
 * center outward toward whichever side the thumb has moved to instead.
 */
class PanTrackDrawable(
    private val trackColor: Int,
    private val fillColor: Int,
    private val trackHeightPx: Float,
    private val cornerRadiusPx: Float
) : Drawable() {

    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = trackColor }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = fillColor }

    override fun draw(canvas: Canvas) {
        val b = bounds
        val top = b.exactCenterY() - trackHeightPx / 2
        val bottom = b.exactCenterY() + trackHeightPx / 2

        canvas.drawRoundRect(
            b.left.toFloat(), top, b.right.toFloat(), bottom,
            cornerRadiusPx, cornerRadiusPx, trackPaint
        )

        val fraction = level / 10000f
        val centerX = b.left + (b.right - b.left) / 2f
        val thumbX = b.left + (b.right - b.left) * fraction

        val left = minOf(centerX, thumbX)
        val right = maxOf(centerX, thumbX)

        if (right > left) {
            canvas.drawRoundRect(left, top, right, bottom, cornerRadiusPx, cornerRadiusPx, fillPaint)
        }
    }

    override fun onLevelChange(level: Int): Boolean {
        invalidateSelf()
        return true
    }

    override fun setAlpha(alpha: Int) {}

    override fun setColorFilter(colorFilter: ColorFilter?) {}

    @Deprecated("Deprecated in Java", ReplaceWith("PixelFormat.TRANSLUCENT"))
    override fun getOpacity() = PixelFormat.TRANSLUCENT
}
