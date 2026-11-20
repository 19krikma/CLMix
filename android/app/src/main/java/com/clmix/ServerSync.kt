package com.clmix

import android.app.Activity
import android.app.Application
import android.content.Intent
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.content.ContextCompat

/**
 * What the phone does while the desktop resyncs with the console (its
 * resync button beside Disconnect).
 *
 * The server sends {"type": "sync", "syncing": true} and drops whatever
 * this phone had selected, since the names and layout it was showing are
 * being replaced. Where the phone goes depends on where it was:
 *
 *  - On Mixer Control (or an EQ / Dynamics screen opened from it) it
 *    stays on Mixer Control - the console's own channels are still the
 *    console's own channels after a resync.
 *  - Riding an aux (or anywhere else) it goes back to the AUX list: the
 *    aux it had open may have been renamed, hidden or taken away.
 *
 * A "Server Syncing" card that cannot be dismissed sits over that screen
 * until the resync is over AND the screen has its new info: "syncing":
 * false makes it ask again (a fresh aux list, or mixer mode and its
 * banks), and the card only comes down when that answer arrives - so
 * nobody touches a strip still showing the old layout.
 *
 * App-wide rather than per screen: it has to move the app between
 * screens, which no one screen can do for the others. It follows the
 * resumed activity through the lifecycle callbacks, so the card is on
 * whichever screen is in front - including the one it navigated to, and
 * one the phone comes back to from the background.
 */
object ServerSync : Application.ActivityLifecycleCallbacks {
    // A resync over a LAN can finish in a few milliseconds. Shown for
    // that long it is a flicker nobody can read, so it stays up at least
    // this long once shown.
    private const val MIN_SHOWN_MS = 800L

    // If the new info never arrives (an older desktop, a dropped frame),
    // the card comes down anyway rather than locking the phone.
    private const val LOAD_TIMEOUT_MS = 8000L

    var syncing = false
        private set

    /** Resync over; waiting for the screen's fresh data. */
    private var loading = false

    /** The screen this sync sends the app to - decided as it starts. */
    private var target: Class<out Activity> = AuxListActivity::class.java

    // Every live activity, oldest first, to tell which flow is on top.
    private val stack = mutableListOf<Activity>()

    private var resumed: Activity? = null
    // The cover laid over the front screen's content, and that screen.
    private var cover: View? = null
    private var coverOwner: Activity? = null
    private var detail: TextView? = null

    // Back does nothing while the cover is up - leaving the screen
    // mid-sync would only land somewhere with nothing loaded.
    private val blockBack = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {}
    }
    private var shownAt = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val finish = Runnable { finishSync() }
    private val giveUp = Runnable { done() }

    /** From MixerClient, on the main thread. */
    fun onSyncState(value: Boolean) {
        if (value) {
            handler.removeCallbacks(finish)
            handler.removeCallbacks(giveUp)
            loading = false

            if (!syncing) {
                syncing = true
                shownAt = SystemClock.uptimeMillis()
                target = pickTarget()
            }

            updateDetail()
            resumed?.let(::follow)
        } else if (syncing && !loading) {
            val shownFor = SystemClock.uptimeMillis() - shownAt
            handler.removeCallbacks(finish)
            handler.postDelayed(finish, (MIN_SHOWN_MS - shownFor).coerceAtLeast(0))
        }
    }

    /** A fresh aux list arrived. */
    fun onAuxes() {
        if (loading && target == AuxListActivity::class.java) done()
    }

    /** A Mixer Control frame arrived - the new channels are on screen. */
    fun onMixerLevels() {
        if (loading && target == MixerControlActivity::class.java) done()
    }

    /** The connection ended - whatever was syncing is moot. */
    fun reset() {
        handler.removeCallbacks(finish)
        handler.removeCallbacks(giveUp)
        syncing = false
        loading = false
        dismiss()
    }

    /**
     * Mixer Control if that is the flow in front, the AUX list otherwise.
     * Walks down from the top screen to the first one that says which:
     * an aux mixer or the aux list means aux, Mixer Control means stay.
     */
    private fun pickTarget(): Class<out Activity> {
        for (activity in stack.asReversed()) {
            when (activity) {
                is MixerActivity, is AuxListActivity -> return AuxListActivity::class.java
                is MixerControlActivity -> return MixerControlActivity::class.java
            }
        }
        return if (MixerClient.mixerControlAllowed) MixerControlActivity::class.java
        else AuxListActivity::class.java
    }

    private fun finishSync() {
        val screen = resumed

        if (screen == null || screen.javaClass != target) {
            // Nothing in front to fill in (the app is in the background,
            // say) - the screen asks for itself when it next resumes.
            done()
            return
        }

        loading = true
        updateDetail()
        handler.postDelayed(giveUp, LOAD_TIMEOUT_MS)

        when (screen) {
            is AuxListActivity -> screen.refreshAuxes()
            is MixerControlActivity -> screen.reselect()
        }
    }

    private fun done() {
        handler.removeCallbacks(giveUp)
        syncing = false
        loading = false
        dismiss()
    }

    /** Take activity to this sync's screen, or show the card on it. */
    private fun follow(activity: Activity) {
        // The login screen has nothing selected to leave, and the server
        // only tells logged-in phones anyway.
        if (activity is ConnectActivity) return

        if (activity.javaClass == target) {
            show(activity)
            return
        }

        // CLEAR_TOP finishes everything above that screen - the
        // mixer, EQ, Dynamics, any sheet open on them - and SINGLE_TOP
        // hands the intent to the instance already there rather than
        // building a new one. It then resumes, and is followed again
        // from there.
        activity.startActivity(
            Intent(activity, target).addFlags(
                Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            )
        )
    }

    private fun updateDetail() {
        detail?.text = when {
            !loading -> "Getting the latest from the console…"
            target == MixerControlActivity::class.java -> "Loading Mixer Control…"
            else -> "Loading AUX list…"
        }
    }

    private fun show(activity: Activity) {
        if (cover != null && coverOwner === activity) return
        dismiss()

        val content = activity.findViewById<ViewGroup>(android.R.id.content) ?: return
        val density = activity.resources.displayMetrics.density
        val view = LayoutInflater.from(activity)
            .inflate(R.layout.dialog_server_sync, content, false)
        view.findViewById<View>(R.id.sync_card).background = GradientDrawable().apply {
            cornerRadius = 24 * density
            setColor(ContextCompat.getColor(activity, R.color.surface))
            setStroke((1 * density).toInt(), ContextCompat.getColor(activity, R.color.divider))
        }
        detail = view.findViewById(R.id.sync_detail)
        updateDetail()

        view.alpha = 0f
        content.addView(view)
        view.animate().alpha(1f).setDuration(150).start()

        cover = view
        coverOwner = activity
        (activity as? ComponentActivity)?.let {
            blockBack.remove()
            it.onBackPressedDispatcher.addCallback(it, blockBack)
            blockBack.isEnabled = true
        }
    }

    private fun dismiss() {
        blockBack.isEnabled = false
        blockBack.remove()
        detail = null
        cover?.let { view ->
            view.animate().cancel()
            (view.parent as? ViewGroup)?.removeView(view)
        }
        cover = null
        coverOwner = null
    }

    override fun onActivityResumed(activity: Activity) {
        resumed = activity
        if (syncing) follow(activity)
    }

    override fun onActivityPaused(activity: Activity) {
        if (resumed !== activity) return

        resumed = null
        // The cover belongs to this screen's content. The next screen to
        // resume puts it back up if the sync is still running.
        dismiss()
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {
        stack.add(activity)
    }

    override fun onActivityDestroyed(activity: Activity) {
        stack.remove(activity)
    }

    override fun onActivityStarted(activity: Activity) {}
    override fun onActivityStopped(activity: Activity) {}
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
}
