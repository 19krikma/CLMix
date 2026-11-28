package com.clmix

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * The connect screen's wallpaper on a tablet, behind ConnectActivity's
 * panel layout: the artwork across the part of the screen the form panel
 * leaves, framed on the lights and the logo, running out under the panel
 * as a blurred, darkened copy of itself - so the whole screen is picture,
 * with no bars and no hard edge. The iOS LoginWideBackgroundView, number
 * for number.
 *
 * The panel is down the right on a tablet on its side, and along the
 * bottom on one held upright ([panelAtBottom]); the artwork takes the rest
 * either way.
 *
 * The artwork is cut for a phone held upright. Filled across the whole of
 * a landscape screen it would be cropped to a strip a third of its height
 * with the logo blown up past the edges; filled across the part left of
 * the panel instead, it keeps the logo whole and most of the lights above.
 */
class LoginWideBackgroundView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val bitmap: Bitmap? =
        BitmapFactory.decodeResource(resources, R.drawable.clmix_login_background)

    private var blurred: Bitmap? = null
    private var blurredForSize = 0L

    private val bitmapPaint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)
    private val veilPaint = Paint().apply {
        color = Color.BLACK
        alpha = (BLUR_DARKENING * 255f).roundToInt()
    }
    private val maskPaint = Paint().apply {
        xfermode = PorterDuffXfermode(PorterDuff.Mode.DST_IN)
    }
    private val drawMatrix = Matrix()

    private var panelSize = 0
    private var panelAtBottom = false

    init {
        setBackgroundColor(Color.BLACK)
    }

    /** The form panel's width (on the right) or height (along the
     *  bottom), in pixels. A no-op when nothing has changed. */
    fun setPanel(sizePx: Int, atBottom: Boolean) {
        if (panelSize == sizePx && panelAtBottom == atBottom) return
        panelSize = sizePx
        panelAtBottom = atBottom
        invalidate()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        blurred = null
    }

    override fun onDraw(canvas: Canvas) {
        val bmp = bitmap ?: return
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return
        val density = resources.displayMetrics.density

        // Under everything: the whole screen as a blurred, darkened copy,
        // there to carry the picture's colour in under the panel.
        blurredFor(bmp, width, height)?.let { soft ->
            drawMatrix.setScale(w / soft.width, h / soft.height)
            canvas.drawBitmap(soft, drawMatrix, bitmapPaint)
        }
        canvas.drawRect(0f, 0f, w, h, veilPaint)

        // The sharp artwork over the part the panel leaves, reaching a
        // little in under it and fading out across the panel's edge, so
        // neither has a seam.
        val underlap = UNDERLAP_DP * density
        val feather = FEATHER_DP * density
        val artW = if (panelAtBottom) w else max(w - panelSize + underlap, 1f)
        val artH = if (panelAtBottom) max(h - panelSize + underlap, 1f) else h

        val layer = canvas.saveLayer(0f, 0f, artW, artH, null)
        drawFocused(canvas, bmp, artW, artH)
        maskPaint.shader = if (panelAtBottom) {
            val start = max(0f, artH - feather)
            LinearGradient(0f, start, 0f, artH, Color.BLACK, Color.TRANSPARENT, Shader.TileMode.CLAMP)
        } else {
            val start = max(0f, artW - feather)
            LinearGradient(start, 0f, artW, 0f, Color.BLACK, Color.TRANSPARENT, Shader.TileMode.CLAMP)
        }
        canvas.drawRect(0f, 0f, artW, artH, maskPaint)
        maskPaint.shader = null
        canvas.restoreToCount(layer)
    }

    /** The artwork filling [w] x [h], cropped about [FOCUS_Y] rather than
     *  its middle: just above the logo, so the lights above it get the
     *  room the floor below it gives up. */
    private fun drawFocused(canvas: Canvas, bmp: Bitmap, w: Float, h: Float) {
        val scale = max(w / bmp.width, h / bmp.height)
        val drawnW = bmp.width * scale
        val drawnH = bmp.height * scale
        val y = min(0f, max(h - drawnH, h / 2f - drawnH * FOCUS_Y))
        val x = (w - drawnW) / 2f
        drawMatrix.setScale(scale, scale)
        drawMatrix.postTranslate(x, y)
        canvas.drawBitmap(bmp, drawMatrix, bitmapPaint)
    }

    /** The blur, by shrinking the artwork and letting it be stretched back
     *  out - see LoginBackgroundView.blurredFor, which this follows. */
    private fun blurredFor(source: Bitmap, w: Int, h: Int): Bitmap? {
        val key = w.toLong() shl 32 or h.toLong()
        blurred?.let { if (blurredForSize == key && !it.isRecycled) return it }
        if (w <= 0 || h <= 0) return null

        val smallW = BLUR_WIDTH_PX
        val smallH = max(1, (smallW.toFloat() * h / w).roundToInt())
        val small = Bitmap.createBitmap(smallW, smallH, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(small)
        val scale = max(smallW.toFloat() / source.width, smallH.toFloat() / source.height)
        val m = Matrix()
        m.setScale(scale, scale)
        m.postTranslate((smallW - source.width * scale) / 2f, (smallH - source.height * scale) / 2f)
        canvas.drawBitmap(source, m, bitmapPaint)

        boxBlur(small, BLUR_PASSES)
        blurred = small
        blurredForSize = key
        return small
    }

    private fun boxBlur(bmp: Bitmap, passes: Int) {
        val w = bmp.width
        val h = bmp.height
        if (w < 3 || h < 3) return
        val a = IntArray(w * h)
        bmp.getPixels(a, 0, w, 0, 0, w, h)
        val b = IntArray(w * h)
        repeat(passes) {
            for (y in 0 until h) for (x in 0 until w) {
                val row = y * w
                b[row + x] = average(a[row + max(0, x - 1)], a[row + x], a[row + min(w - 1, x + 1)])
            }
            for (x in 0 until w) for (y in 0 until h) {
                a[y * w + x] = average(b[max(0, y - 1) * w + x], b[y * w + x], b[min(h - 1, y + 1) * w + x])
            }
        }
        bmp.setPixels(a, 0, w, 0, 0, w, h)
    }

    private fun average(a: Int, b: Int, c: Int): Int = Color.rgb(
        ((a shr 16 and 0xFF) + (b shr 16 and 0xFF) + (c shr 16 and 0xFF)) / 3,
        ((a shr 8 and 0xFF) + (b shr 8 and 0xFF) + (c shr 8 and 0xFF)) / 3,
        ((a and 0xFF) + (b and 0xFF) + (c and 0xFF)) / 3,
    )

    companion object {
        private const val FOCUS_Y = 0.45f
        private const val UNDERLAP_DP = 40f
        private const val FEATHER_DP = 100f
        private const val BLUR_DARKENING = 0.5f
        // Smaller than the phone band's 64: the iOS copy is blurred by
        // 50pt, far softer than the phone's frost.
        private const val BLUR_WIDTH_PX = 32
        private const val BLUR_PASSES = 3
    }
}

/**
 * The tablet form panel's backing: dark glass over the wallpaper's
 * blurred copy, with a hairline along the edge that faces the artwork so
 * the panel reads as a surface rather than as a darker patch of picture.
 * The iOS LoginSidePanelBackground.
 */
class LoginPanelDrawable(
    private val edgeColor: Int,
    private val edgeAtTop: Boolean,
    private val hairlinePx: Float,
) : android.graphics.drawable.Drawable() {
    private val fill = Paint().apply { color = Color.argb((0.45f * 255).roundToInt(), 0, 0, 0) }
    private val edge = Paint().apply {
        color = edgeColor
        alpha = (Color.alpha(edgeColor) * 0.5f).roundToInt()
    }

    override fun draw(canvas: Canvas) {
        val b = bounds
        canvas.drawRect(b, fill)
        if (edgeAtTop) {
            canvas.drawRect(b.left.toFloat(), b.top.toFloat(), b.right.toFloat(), b.top + hairlinePx, edge)
        } else {
            canvas.drawRect(b.left.toFloat(), b.top.toFloat(), b.left + hairlinePx, b.bottom.toFloat(), edge)
        }
    }

    override fun setAlpha(alpha: Int) {}
    override fun setColorFilter(colorFilter: android.graphics.ColorFilter?) {}
    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = android.graphics.PixelFormat.TRANSLUCENT
}
