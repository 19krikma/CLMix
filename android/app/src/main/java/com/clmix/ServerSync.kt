package com.clmix

import android.app.Activity
import android.app.Application
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AlertDialog

/**
 * What the phone does while the desktop resyncs with the console (its
 * resync button beside Disconnect).
 *
 * The server sends {"type": "sync", "syncing": true} and drops whatever
 * this phone had selected, since the names and layout it was showing are
 * being replaced. So the app goes back to where a choice is made - the
 * AUX Only / Mixer Control screen for an account that has that choice,
 * the aux list for everyone else - and holds a "Server Syncing" spinner
 * over it that cannot be dismissed. "syncing": false takes it down, and
 * the aux list asks for a fresh list, since aux names may have changed.
 *
 * App-wide rather than per screen: it has to move the app between
 * screens, which no one screen can do for the others. It follows the
 * resumed activity through the lifecycle callbacks, so the spinner is on
 * whichever screen is in front - including the one it navigated to, and
 * one the phone comes back to from the background.
 */
object ServerSync : Application.ActivityLifecycleCallbacks {
    // A resync over a LAN can finish in a few milliseconds. Shown for
    // that long it is a flicker nobody can read, so it stays up at least
    // this long once shown.
    private const val MIN_SHOWN_MS = 800L

    var syncing = false
        private set

    private var resumed: Activity? = null
    private var dialog: AlertDialog? = null
    private var shownAt = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val finish = Runnable { finishSync() }

    /** From MixerClient, on the main thread. */
    fun onSyncState(value: Boolean) {
        if (value) {
            handler.removeCallbacks(finish)

            if (!syncing) {
                syncing = true
                shownAt = SystemClock.uptimeMillis()
            }

            resumed?.let(::follow)
        } else if (syncing) {
            val shownFor = SystemClock.uptimeMillis() - shownAt
            handler.removeCallbacks(finish)
            handler.postDelayed(finish, (MIN_SHOWN_MS - shownFor).coerceAtLeast(0))
        }
    }

    /** The connection ended - whatever was syncing is moot. */
    fun reset() {
        handler.removeCallbacks(finish)
        syncing = false
        dismiss()
    }

    private fun finishSync() {
        syncing = false
        dismiss()
        (resumed as? AuxListActivity)?.refreshAuxes()
    }

    private fun home(): Class<out Activity> =
        if (MixerClient.mixerControlAllowed) ControlChoiceActivity::class.java
        else AuxListActivity::class.java

    /** Take activity back to the choice screen, or show the spinner on it. */
    private fun follow(activity: Activity) {
        // The login screen has nothing selected to leave, and the server
        // only tells logged-in phones anyway.
        if (activity is ConnectActivity) return

        if (activity.javaClass == home()) {
            show(activity)
            return
        }

        // CLEAR_TOP finishes everything above the choice screen - the
        // mixer, EQ, Dynamics, any sheet open on them - and SINGLE_TOP
        // hands the intent to the instance already there rather than
        // building a new one, which keeps the aux list it was opened
        // with. It then resumes, and is followed again from there.
        activity.startActivity(
            Intent(activity, home()).addFlags(
                Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            )
        )
    }

    private fun show(activity: Activity) {
        if (dialog?.isShowing == true && dialog?.ownerActivity === activity) return
        dismiss()

        val pad = (24 * activity.resources.displayMetrics.density).toInt()

        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(pad, pad, pad, pad)
            addView(ProgressBar(activity).apply { isIndeterminate = true })
            addView(TextView(activity).apply {
                text = "Server Syncing"
                textSize = 18f
                setPadding(pad, 0, 0, 0)
            })
        }

        dialog = AlertDialog.Builder(activity)
            .setView(content)
            .setCancelable(false)
            .create()
            .also {
                it.setCanceledOnTouchOutside(false)
                it.setOwnerActivity(activity)
                it.show()
            }
    }

    private fun dismiss() {
        dialog?.let { if (it.isShowing) it.dismiss() }
        dialog = null
    }

    override fun onActivityResumed(activity: Activity) {
        resumed = activity
        if (syncing) follow(activity)
    }

    override fun onActivityPaused(activity: Activity) {
        if (resumed !== activity) return

        resumed = null
        // A dialog must not outlive its window. The next screen to resume
        // puts it back up if the sync is still running.
        dismiss()
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
    override fun onActivityStarted(activity: Activity) {}
    override fun onActivityStopped(activity: Activity) {}
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
    override fun onActivityDestroyed(activity: Activity) {}
}
