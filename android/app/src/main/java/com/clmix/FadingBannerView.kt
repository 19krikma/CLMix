package com.clmix

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RectF
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * The connect screen's header: the CLMix feature graphic pinned to the very
 * top, drawn full-bleed at its own aspect ratio, dissolving into whatever the
 * activity's background happens to be.
 *
 * The dissolve is done by erasing the view's own alpha (DST_OUT) rather than
 * by painting a coloured scrim over it, so the graphic melts into the day
 * palette and the night one alike without either being named here - what
 * shows through the erased part is just the window background.
 *
 * The graphic's bottom [TAIL_START] of its height is solid black, so once the
 * artwork runs out the view keeps filling black down to its bottom edge: the
 * seam is invisible and the fade can be stretched well past the image itself.
 * That matters because the fade is positioned against the *content* on top of
 * this view (it runs from the Discovered box down to Manual - see
 * ConnectActivity), not against the artwork's own proportions.
 */
class FadingBannerView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var bitmap: Bitmap? = null

    private val bitmapPaint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)

    // Matches the artwork's solid-black lower edge, so the stretch below the
    // image reads as more of the same picture rather than as a second block.
    private val tailPaint = Paint().apply { color = Color.BLACK }

    private val fadePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        xfermode = PorterDuffXfermode(PorterDuff.Mode.DST_OUT)
    }

    private val dstRect = RectF()

    // Where the dissolve begins and where it has finished, in this view's own
    // pixels. Both are supplied from outside once the content above has been
    // laid out; until then the view is simply opaque.
    private var fadeStart = 0f
    private var fadeEnd = 0f

    /** Height the artwork itself occupies once scaled to the view's width. */
    var imageHeight = 0
        private set

    init {
        if (attrs != null) {
            val a = context.obtainStyledAttributes(attrs, intArrayOf(android.R.attr.src))
            val resId = a.getResourceId(0, 0)
            a.recycle()
            if (resId != 0) setBannerResource(resId)
        }
    }

    fun setBannerResource(resId: Int) {
        // Decoded straight to a Bitmap (rather than kept as a Drawable) so the
        // draw below can scale it to the exact full-bleed rect with bilinear
        // filtering, which a plain drawable bounds-scale doesn't guarantee.
        bitmap = BitmapFactory.decodeResource(resources, resId)
        requestLayout()
        invalidate()
    }

    /**
     * Pins the dissolve to two absolute y positions inside this view. Both are
     * measured from the top of the banner, which is also the top of the
     * screen. A no-op when nothing has moved, so this is safe to call from a
     * layout pass.
     */
    fun setFadeBounds(start: Float, end: Float) {
        val safeEnd = max(end, start + 1f)
        if (fadeStart == start && fadeEnd == safeEnd) return
        fadeStart = start
        fadeEnd = safeEnd
        fadePaint.shader = null
        requestLayout()
        invalidate()
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        val bmp = bitmap
        imageHeight = if (bmp != null && bmp.width > 0) {
            (width.toFloat() * bmp.height / bmp.width).roundToInt()
        } else {
            0
        }
        // Tall enough to carry the fade all the way to its end - past the
        // artwork's own bottom when the content on top asks for it.
        val height = max(imageHeight, ceil(fadeEnd).toInt())
        setMeasuredDimension(width, height)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        fadePaint.shader = null
    }

    override fun onDraw(canvas: Canvas) {
        val w = width
        val h = height
        if (w <= 0 || h <= 0) return

        // The whole banner is composed off-screen first: DST_OUT has to erase
        // the assembled artwork-plus-tail, not punch through to the window
        // behind it one layer at a time.
        val layer = canvas.saveLayer(0f, 0f, w.toFloat(), h.toFloat(), null)

        bitmap?.let {
            dstRect.set(0f, 0f, w.toFloat(), imageHeight.toFloat())
            canvas.drawBitmap(it, null, dstRect, bitmapPaint)
        }
        if (h > imageHeight) {
            // Overlaps the artwork's last row by a pixel so no hairline of
            // window background shows through at the join.
            canvas.drawRect(0f, (imageHeight - 1).toFloat(), w.toFloat(), h.toFloat(), tailPaint)
        }

        if (fadeEnd > fadeStart) {
            if (fadePaint.shader == null) fadePaint.shader = buildFadeShader(w)
            canvas.drawRect(0f, fadeStart, w.toFloat(), h.toFloat(), fadePaint)
        }

        canvas.restoreToCount(layer)
    }

    private fun buildFadeShader(width: Int): Shader {
        // Eased rather than linear: the graphic gives up most of its opacity
        // early on, which keeps the Discovered box's own label - dark-on-light
        // in day mode - readable where it sits over the top of the fade,
        // while the last of the black still doesn't clear until fadeEnd.
        val steps = 12
        val colors = IntArray(steps + 1)
        val stops = FloatArray(steps + 1)
        for (i in 0..steps) {
            val t = i.toFloat() / steps
            val erased = 1f - (1f - t) * (1f - t)
            stops[i] = t
            colors[i] = Color.argb((erased * 255f).roundToInt(), 0, 0, 0)
        }
        return LinearGradient(
            0f, fadeStart, 0f, fadeEnd,
            colors, stops, Shader.TileMode.CLAMP
        )
    }

    companion object {
        /**
         * Fraction of the artwork's height above which anything is drawn; the
         * rest of it is solid black. ConnectActivity uses this to drop its
         * content in just below the logo instead of on top of it.
         */
        const val TAIL_START = 0.858f
    }
}
