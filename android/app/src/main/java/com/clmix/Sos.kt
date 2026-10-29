package com.clmix

import android.annotation.SuppressLint
import android.app.Activity
import android.app.Application
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlin.math.abs

/**
 * The SOS button: on every screen after login, always on top. Tapping it
 * offers DEAD BATTERY, AUX NOT WORKING, NEED ASSISTANCE or CANCEL; the
 * first three go to the server ({"action": "sos", "reason": ...}), which
 * pops an SOS window up on the sound engineer's desktop saying what is
 * needed and who asked.
 *
 * App-wide rather than per screen, like ServerSync: the lifecycle
 * callbacks lay the button over each activity's content as it resumes, so
 * no layout has to make room for it. It starts in the middle of the top
 * edge - the empty middle of the mixer's top bar - and can be dragged
 * anywhere it is less in the way; where it was left is remembered.
 */
object Sos : Application.ActivityLifecycleCallbacks {
    // Wire values, as services/remote_server.py's SOS_REASONS knows them,
    // in the order offered.
    private val REASONS = listOf(
        "dead_battery" to "DEAD BATTERY",
        "aux_not_working" to "AUX NOT WORKING",
        "need_assistance" to "NEED ASSISTANCE",
    )

    // No answer by then and the SOS is reported as not delivered: the
    // connection is down, or the desktop is too old to know SOS.
    private const val REPLY_TIMEOUT_MS = 6000L

    private const val TAG = "clmix_sos_button"
    private const val PREFS = "sos"
    private const val KEY_X = "x"
    private const val KEY_Y = "y"

    private var appContext: Context? = null
    private var resumed: Activity? = null
    private val handler = Handler(Looper.getMainLooper())
    private var pending = false
    private val timeout = Runnable {
        if (pending) {
            pending = false
            toast("SOS not delivered - find the sound engineer")
        }
    }

    fun init(context: Context) {
        appContext = context.applicationContext
    }

    // --- From MixerClient, on the main thread ---

    fun onSent() {
        if (!pending) return
        pending = false
        handler.removeCallbacks(timeout)
        toast("SOS sent - the sound engineer has been notified")
    }

    /** Takes a server error that is about an SOS, so it is reported as
     *  one rather than as a stray error on whatever screen is up. */
    fun claimsError(message: String): Boolean {
        if (!pending) return false
        if (!message.contains("sos", ignoreCase = true)) return false

        pending = false
        handler.removeCallbacks(timeout)
        // "unknown action 'sos'": a desktop from before SOS existed.
        toast(
            if (message.startsWith("unknown action")) {
                "SOS not delivered - CLMix on the computer needs updating"
            } else {
                message
            }
        )
        return true
    }

    // --- The button ---

    override fun onActivityResumed(activity: Activity) {
        resumed = activity
        // Before login there is no one to send an SOS to.
        if (activity is ConnectActivity) return

        val content = activity.findViewById<FrameLayout>(android.R.id.content) ?: return
        val button = content.findViewWithTag<View>(TAG) ?: addButton(activity, content)
        // Over anything the screen added to its content since.
        button.bringToFront()
    }

    override fun onActivityPaused(activity: Activity) {
        if (resumed === activity) resumed = null
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun addButton(activity: Activity, content: FrameLayout): View {
        val density = activity.resources.displayMetrics.density
        val button = TextView(activity).apply {
            tag = TAG
            text = "SOS"
            contentDescription = "SOS - call the sound engineer"
            setTextColor(Color.WHITE)
            textSize = 14f
            typeface = Typeface.DEFAULT_BOLD
            letterSpacing = 0.1f
            gravity = Gravity.CENTER
            minWidth = (64 * density).toInt()
            minHeight = (36 * density).toInt()
            setPadding((14 * density).toInt(), 0, (14 * density).toInt(), 0)
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#C62828"))
                cornerRadius = 18 * density
                setStroke((1 * density).toInt(), Color.parseColor("#FF8A80"))
            }
            elevation = 8 * density
        }

        content.addView(
            button,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.START
            )
        )

        // Placed by translation rather than layout params, so re-placing
        // it never asks for a layout pass.
        val place = { position(content, button) }
        content.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> place() }
        button.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> place() }

        val slop = ViewConfiguration.get(activity).scaledTouchSlop
        var downX = 0f
        var downY = 0f
        var startX = 0f
        var startY = 0f
        var dragging = false

        button.setOnTouchListener { view, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX
                    downY = event.rawY
                    startX = view.translationX
                    startY = view.translationY
                    dragging = false
                    view.alpha = 0.8f
                }

                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downX
                    val dy = event.rawY - downY
                    if (!dragging && (abs(dx) > slop || abs(dy) > slop)) {
                        dragging = true
                        // Not a drag for whatever is underneath, either.
                        view.parent.requestDisallowInterceptTouchEvent(true)
                    }
                    if (dragging) {
                        val (left, top, right, bottom) = bounds(content, view)
                        view.translationX = (startX + dx).coerceIn(left, right)
                        view.translationY = (startY + dy).coerceIn(top, bottom)
                    }
                }

                MotionEvent.ACTION_UP -> {
                    view.alpha = 1f
                    if (dragging) {
                        remember(content, view)
                    } else {
                        view.performClick()
                    }
                }

                MotionEvent.ACTION_CANCEL -> view.alpha = 1f
            }
            true
        }

        button.setOnClickListener { showOptions(activity) }
        return button
    }

    /** Where the button may go: inside the content, clear of the status
     *  and navigation bars and any cutout - none on screens that hide
     *  them. As left, top, right, bottom for the button's top-left. */
    private fun bounds(content: View, button: View): List<Float> {
        val insets = ViewCompat.getRootWindowInsets(content)?.getInsets(
            WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
        )
        val left = (insets?.left ?: 0).toFloat()
        val top = (insets?.top ?: 0).toFloat()
        val right = (content.width - (insets?.right ?: 0) - button.width).toFloat()
        val bottom = (content.height - (insets?.bottom ?: 0) - button.height).toFloat()
        return listOf(left, top, right.coerceAtLeast(left), bottom.coerceAtLeast(top))
    }

    /** Puts the button where it was last left, as a share of the room it
     *  has - so it lands in the same place on every screen and in either
     *  orientation. Never moved: the middle of the top edge. */
    private fun position(content: View, button: View) {
        if (content.width == 0 || button.width == 0) return
        val prefs = content.context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val (left, top, right, bottom) = bounds(content, button)
        val fx = prefs.getFloat(KEY_X, 0.5f)
        val fy = prefs.getFloat(KEY_Y, -1f)
        val density = content.resources.displayMetrics.density

        button.translationX = left + (right - left) * fx
        button.translationY = if (fy < 0f) {
            // Lines up with the mixer's top bar (6dp padding, 40dp icons).
            (top + 8 * density).coerceAtMost(bottom)
        } else {
            top + (bottom - top) * fy
        }
    }

    private fun remember(content: View, button: View) {
        val (left, top, right, bottom) = bounds(content, button)
        val fx = if (right > left) (button.translationX - left) / (right - left) else 0.5f
        val fy = if (bottom > top) (button.translationY - top) / (bottom - top) else 0f
        content.context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putFloat(KEY_X, fx.coerceIn(0f, 1f))
            .putFloat(KEY_Y, fy.coerceIn(0f, 1f))
            .apply()
    }

    // --- The options ---

    private fun showOptions(activity: Activity) {
        if (activity.isFinishing) return

        MaterialAlertDialogBuilder(activity)
            .setTitle("SOS - what do you need?")
            .setItems(REASONS.map { it.second }.toTypedArray()) { _, which ->
                send(REASONS[which].first)
            }
            .setNegativeButton("CANCEL", null)
            .show()
    }

    private fun send(reason: String) {
        pending = true
        handler.removeCallbacks(timeout)
        handler.postDelayed(timeout, REPLY_TIMEOUT_MS)
        MixerClient.sendSos(reason)
    }

    private fun toast(text: String) {
        val context = resumed ?: appContext ?: return
        Toast.makeText(context, text, Toast.LENGTH_LONG).show()
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
    override fun onActivityStarted(activity: Activity) {}
    override fun onActivityStopped(activity: Activity) {}
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
    override fun onActivityDestroyed(activity: Activity) {}
}
