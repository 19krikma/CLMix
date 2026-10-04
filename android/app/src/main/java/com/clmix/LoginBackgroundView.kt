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
 * The connect screen's wallpaper: the CLMix artwork drawn full-bleed behind
 * the whole form, with a frosted, darkened band down the middle so the boxes,
 * fields and button on top of it stay readable. The iOS LoginBackgroundView,
 * number for number - see it for why the band is shaped the way it is.
 *
 * In short: the artwork is a photograph's worth of detail and an outlined
 * text field over any of it is unreadable, so rather than boxing the form
 * into an opaque panel the frost is masked - full strength down the column
 * the controls occupy, easing away towards the screen's edges, where the
 * picture is left as it is.
 *
 * Unlike the banner this replaces there is no night variant and none is
 * wanted: the artwork is black, so ConnectActivity pins itself to the dark
 * palette in both themes.
 *
 * Nothing here animates or observes anything. The frost is composed once per
 * size change and the view then sits still, which is why a saveLayer in
 * [onDraw] costs nothing worth saving.
 */
class LoginBackgroundView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var bitmap: Bitmap? = null

    // The blurred copy, at a fraction of the view's size - it is drawn back
    // out scaled up, which is half of where the blur comes from (see
    // buildBlurred). Rebuilt on a size change and otherwise reused.
    private var blurred: Bitmap? = null
    private var blurredForSize = 0L

    private val bitmapPaint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)
    private val blurPaint = Paint(Paint.FILTER_BITMAP_FLAG).apply {
        alpha = (BLUR_STRENGTH * 255f).roundToInt()
    }
    private val darkenPaint = Paint().apply {
        color = Color.BLACK
        alpha = (DARKENING * 255f).roundToInt()
    }
    private val maskPaint = Paint().apply {
        xfermode = PorterDuffXfermode(PorterDuff.Mode.DST_IN)
    }

    private val drawMatrix = Matrix()

    /**
     * Where the form's first box starts, in pixels from the top of the view.
     * The frost fades in just above it, so the strip of artwork over the form
     * - the hanging lights, the best of the picture - is left untouched.
     */
    private var bandTop = 0f

    init {
        bitmap = BitmapFactory.decodeResource(resources, R.drawable.clmix_login_background)
        // The artwork's own corners are black, so anything the image does not
        // reach - an aspect ratio it was never cut for - reads as more
        // picture rather than as a gap.
        setBackgroundColor(Color.BLACK)
    }

    /**
     * Pins the band to where the form actually starts. A no-op when nothing
     * has moved, so this is safe to call from a layout pass.
     */
    fun setBandTop(top: Float) {
        if (bandTop == top) return
        bandTop = top
        invalidate()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        blurred = null
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        val bmp = bitmap ?: return
        if (w <= 0f || h <= 0f) return

        // Centred across and pinned to the top, filling both ways: the crop
        // is then predictable off the phone portrait ratio the artwork was
        // cut to, and landscape shows the top of the picture (lights over
        // black) rather than whichever slice happened to land in the middle.
        val scale = max(w / bmp.width, h / bmp.height)
        drawMatrix.setScale(scale, scale)
        drawMatrix.postTranslate((w - bmp.width * scale) / 2f, 0f)
        canvas.drawBitmap(bmp, drawMatrix, bitmapPaint)

        // The band is composed off-screen so the two mask passes erase the
        // assembled blur-plus-black, rather than punching through to the
        // sharp picture one layer at a time.
        val layer = canvas.saveLayer(0f, 0f, w, h, null)

        val soft = blurredFor(bmp, width, height)
        if (soft != null) {
            drawMatrix.setScale(w / soft.width, h / soft.height)
            canvas.drawBitmap(soft, drawMatrix, blurPaint)
        }
        canvas.drawRect(0f, 0f, w, h, darkenPaint)

        // Two DST_IN passes in a row multiply the destination's alpha by both
        // gradients in turn, which is the across-and-down mask the iOS view
        // gets from nesting one mask inside the other.
        maskPaint.shader = sideShader(w)
        canvas.drawRect(0f, 0f, w, h, maskPaint)
        maskPaint.shader = verticalShader(h)
        canvas.drawRect(0f, 0f, w, h, maskPaint)
        maskPaint.shader = null

        canvas.restoreToCount(layer)
    }

    // MARK: the blur

    /**
     * The blur, done by shrinking the artwork and letting it be stretched
     * back out: the downscale averages each block of pixels into one, the
     * box passes below take the corners off the blocks, and the bilinear
     * upscale on the way out smooths what is left. A RenderEffect would be a
     * truer gaussian but only exists from API 31, and this view has to work
     * back to 24 - where the alternatives are RenderScript (removed) or
     * blurring two million pixels in a loop.
     */
    private fun blurredFor(source: Bitmap, w: Int, h: Int): Bitmap? {
        val key = w.toLong() shl 32 or h.toLong()
        blurred?.let { if (blurredForSize == key && !it.isRecycled) return it }
        if (w <= 0 || h <= 0) return null

        // Fixed width rather than a fraction of the screen's, so the blur is
        // the same strength relative to the picture on every device: a phone
        // with more pixels gets a bigger kernel, not a sharper band.
        val smallW = BLUR_WIDTH_PX
        val smallH = max(1, (smallW.toFloat() * h / w).roundToInt())

        val small = Bitmap.createBitmap(smallW, smallH, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(small)
        val scale = max(smallW.toFloat() / source.width, smallH.toFloat() / source.height)
        val m = Matrix()
        m.setScale(scale, scale)
        m.postTranslate((smallW - source.width * scale) / 2f, 0f)
        canvas.drawBitmap(source, m, bitmapPaint)

        boxBlur(small, BLUR_PASSES)

        blurred = small
        blurredForSize = key
        return small
    }

    /** Separable box blur of radius 1, run [passes] times. Three passes of a
     *  box are a close enough gaussian, and on a bitmap this small the whole
     *  thing is a few thousand additions. */
    private fun boxBlur(bmp: Bitmap, passes: Int) {
        val w = bmp.width
        val h = bmp.height
        if (w < 3 || h < 3) return
        val pixels = IntArray(w * h)
        bmp.getPixels(pixels, 0, w, 0, 0, w, h)
        val scratch = IntArray(w * h)

        repeat(passes) {
            blurRows(pixels, scratch, w, h)
            // Rows again, reading down the columns instead: the transpose is
            // done by the index arithmetic rather than by a second routine.
            blurColumns(scratch, pixels, w, h)
        }
        bmp.setPixels(pixels, 0, w, 0, 0, w, h)
    }

    private fun blurRows(src: IntArray, dst: IntArray, w: Int, h: Int) {
        for (y in 0 until h) {
            val row = y * w
            for (x in 0 until w) {
                val a = src[row + max(0, x - 1)]
                val b = src[row + x]
                val c = src[row + min(w - 1, x + 1)]
                dst[row + x] = average(a, b, c)
            }
        }
    }

    private fun blurColumns(src: IntArray, dst: IntArray, w: Int, h: Int) {
        for (x in 0 until w) {
            for (y in 0 until h) {
                val a = src[max(0, y - 1) * w + x]
                val b = src[y * w + x]
                val c = src[min(h - 1, y + 1) * w + x]
                dst[y * w + x] = average(a, b, c)
            }
        }
    }

    private fun average(a: Int, b: Int, c: Int): Int {
        val r = ((a shr 16 and 0xFF) + (b shr 16 and 0xFF) + (c shr 16 and 0xFF)) / 3
        val g = ((a shr 8 and 0xFF) + (b shr 8 and 0xFF) + (c shr 8 and 0xFF)) / 3
        val bl = ((a and 0xFF) + (b and 0xFF) + (c and 0xFF)) / 3
        return Color.rgb(r, g, bl)
    }

    // MARK: the mask

    /**
     * Full strength across the middle [SIDE_HOLD] of the width, then
     * smoothstepped out to nothing at both edges. Smoothstep rather than
     * linear because a linear ramp leaves a visible line where it reaches
     * full strength - the eye finds the corner in a gradient even when it
     * cannot see the gradient itself.
     */
    private fun sideShader(w: Float): Shader {
        val steps = 24
        val colors = IntArray(steps + 1)
        val stops = FloatArray(steps + 1)
        for (i in 0..steps) {
            val t = i.toFloat() / steps
            val offCentre = kotlin.math.abs(t - 0.5f) * 2f
            val alpha = if (offCentre <= SIDE_HOLD) {
                1f
            } else {
                1f - smoothstep((offCentre - SIDE_HOLD) / (1f - SIDE_HOLD))
            }
            stops[i] = t
            colors[i] = Color.argb((alpha * 255f).roundToInt(), 0, 0, 0)
        }
        return LinearGradient(0f, 0f, w, 0f, colors, stops, Shader.TileMode.CLAMP)
    }

    /**
     * Up above the form, held through it, and off again over the floor: the
     * reflections along the bottom are the other half of the artwork, and
     * nothing but the login button - an opaque fill, which needs no help from
     * the band - is ever down there.
     */
    private fun verticalShader(h: Float): Shader {
        val steps = 8
        val density = resources.displayMetrics.density
        val rampStart = (bandTop + FADE_IN_START_DP * density) / h
        val rampEnd = (bandTop + FADE_IN_END_DP * density) / h

        val colors = ArrayList<Int>(steps * 2 + 2)
        val stops = ArrayList<Float>(steps * 2 + 2)

        for (i in 0..steps) {
            val t = i.toFloat() / steps
            stops += min(max(rampStart + (rampEnd - rampStart) * t, 0f), 1f)
            colors += Color.argb((smoothstep(t) * 255f).roundToInt(), 0, 0, 0)
        }
        stops += TAIL_START
        colors += Color.BLACK
        for (i in 1..steps) {
            val t = i.toFloat() / steps
            stops += TAIL_START + (1f - TAIL_START) * t
            colors += Color.argb(
                ((1f - (1f - TAIL_FLOOR) * smoothstep(t)) * 255f).roundToInt(), 0, 0, 0
            )
        }

        return LinearGradient(
            0f, 0f, 0f, h,
            colors.toIntArray(), stops.toFloatArray(), Shader.TileMode.CLAMP
        )
    }

    private fun smoothstep(t: Float): Float {
        val c = min(max(t, 0f), 1f)
        return c * c * (3f - 2f * c)
    }

    companion object {
        /**
         * Fraction of the screen's height left clear above the form.
         * ConnectActivity pads its content down by this and hands the same
         * number back as the band top, so the two cannot drift apart.
         */
        const val CONTENT_TOP_FRACTION = 0.20f

        /**
         * How far above the band top the frost starts coming up, and how
         * far above it the frost has reached full strength. Both
         * negative: the ramp finishes just before the first box rather
         * than inside it, so no control sits half on and half off the
         * band.
         */
        private const val FADE_IN_START_DP = -130f
        private const val FADE_IN_END_DP = -10f

        /**
         * Fraction of the half-width held at full strength before the
         * sideways fade begins.
         *
         * Deliberately wide. The obvious value is something near half, so the
         * fade has most of the margin to run in - but the logo in this
         * artwork is brushed nearly edge to edge, and a fade that clears that
         * early cuts the bright tips of the C and the x out of the frost and
         * leaves them hanging crisply beside the boxes.
         */
        private const val SIDE_HOLD = 0.72f

        /** Where the frost starts easing off towards the bottom of the
         *  screen, and how much of it is left at the bottom edge. */
        private const val TAIL_START = 0.82f
        private const val TAIL_FLOOR = 0.30f

        /** How dark the band goes, on top of the blur, and how much of the
         *  blur is let through over the sharp picture underneath. */
        private const val DARKENING = 0.48f
        private const val BLUR_STRENGTH = 0.80f

        /** Width the artwork is shrunk to before being stretched back over
         *  the screen - the blur's radius, in effect. */
        private const val BLUR_WIDTH_PX = 64
        private const val BLUR_PASSES = 3
    }
}
