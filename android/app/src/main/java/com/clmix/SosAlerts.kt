package com.clmix

import android.app.Activity
import android.app.Application
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import org.json.JSONArray
import org.json.JSONObject
import java.text.DateFormat
import java.util.Date

/**
 * SOS calls on a sound tech's phone - an account holding Mixer Control.
 *
 * When a performer presses SOS, the server shows it to every sound tech
 * as well as on the desktop ({"type": "sos_alert"}), and gives a sound
 * tech logging in the ones still open (login_result "sos_alerts"). Each
 * is a red card over whatever screen is up, which stays - taking every
 * touch, and Back - until it is cleared: Clear here, on another sound
 * tech's phone, or "Got it" on the desktop ({"type": "sos_cleared"}).
 * Several open at once are shown one at a time, oldest first.
 *
 * App-wide, like ServerSync, so it is on whichever screen is in front;
 * it follows the resumed activity through the lifecycle callbacks.
 */
object SosAlerts : Application.ActivityLifecycleCallbacks {
    private data class Alert(
        val id: Int,
        val name: String,
        val reason: String,
        val where: String?,
        val time: Double,
        val count: Int,
    )

    private val alerts = linkedMapOf<Int, Alert>()
    private var resumed: Activity? = null
    private var cover: View? = null
    private var coverOwner: Activity? = null
    private var shownId: Int? = null

    private val blockBack = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {}
    }

    // --- From MixerClient, on the main thread ---

    /** The open ones a sound tech logs in to; none for anyone else. */
    fun onLogin(array: JSONArray?) {
        alerts.clear()
        if (array != null) {
            for (i in 0 until array.length()) array.optJSONObject(i)?.let(::add)
        }
        refresh()
    }

    fun onAlert(json: JSONObject) {
        json.optJSONObject("alert")?.let(::add)
        refresh()
    }

    fun onCleared(json: JSONObject) {
        val id = json.optInt("id", -1)
        // Gone already if this phone cleared it; otherwise say who did.
        if (alerts.remove(id) != null) {
            resumed?.let {
                val by = json.optString("by").ifEmpty { "another sound tech" }
                Toast.makeText(it, "SOS cleared by $by", Toast.LENGTH_SHORT).show()
            }
        }
        refresh()
    }

    /** The connection ended, or another account may log in next. */
    fun reset() {
        alerts.clear()
        refresh()
    }

    private fun add(o: JSONObject) {
        val id = o.optInt("id", -1)
        if (id < 0) return
        alerts[id] = Alert(
            id = id,
            name = o.optString("name").ifEmpty { o.optString("user") },
            reason = o.optString("reason"),
            where = o.optString("aux_name").takeIf { !o.isNull("aux_name") && it.isNotEmpty() },
            time = o.optDouble("time", 0.0),
            count = o.optInt("count", 1),
        )
    }

    private fun clear(id: Int) {
        alerts.remove(id)
        MixerClient.clearSos(id)
        refresh()
    }

    /** Puts the oldest open alert on the front screen, or takes it away. */
    private fun refresh() {
        val alert = alerts.values.firstOrNull()
        val activity = resumed

        if (alert == null || activity == null || activity is ConnectActivity) {
            dismiss()
            return
        }

        if (cover == null || coverOwner !== activity) {
            dismiss()
            show(activity)
        }
        fill(alert)
    }

    private fun show(activity: Activity) {
        val content = activity.findViewById<ViewGroup>(android.R.id.content) ?: return
        val density = activity.resources.displayMetrics.density
        val view = LayoutInflater.from(activity).inflate(R.layout.overlay_sos_alert, content, false)

        view.findViewById<View>(R.id.sos_card).background = GradientDrawable().apply {
            cornerRadius = 24 * density
            setColor(Color.parseColor("#C62828"))
        }
        view.findViewById<View>(R.id.sos_clear_button).setOnClickListener {
            shownId?.let(::clear)
        }

        content.addView(view)
        cover = view
        coverOwner = activity

        (activity as? ComponentActivity)?.let {
            blockBack.remove()
            it.onBackPressedDispatcher.addCallback(it, blockBack)
            blockBack.isEnabled = true
        }
    }

    private fun fill(alert: Alert) {
        val view = cover ?: return
        shownId = alert.id

        view.findViewById<TextView>(R.id.sos_reason).text = alert.reason
        view.findViewById<TextView>(R.id.sos_from).text = "from ${alert.name}"

        val at = DateFormat.getTimeInstance(DateFormat.SHORT).format(Date((alert.time * 1000).toLong()))
        val asked = if (alert.count > 1) "asked ${alert.count} times, last at $at" else "at $at"
        view.findViewById<TextView>(R.id.sos_details).text =
            listOfNotNull(alert.where, asked).joinToString("  ·  ")

        val waiting = alerts.size - 1
        view.findViewById<TextView>(R.id.sos_more).apply {
            visibility = if (waiting > 0) View.VISIBLE else View.GONE
            text = "+$waiting more SOS waiting"
        }
    }

    private fun dismiss() {
        blockBack.isEnabled = false
        blockBack.remove()
        cover?.let { (it.parent as? ViewGroup)?.removeView(it) }
        cover = null
        coverOwner = null
        shownId = null
    }

    override fun onActivityResumed(activity: Activity) {
        resumed = activity
        refresh()
    }

    override fun onActivityPaused(activity: Activity) {
        if (resumed !== activity) return
        resumed = null
        dismiss()
    }

    override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
    override fun onActivityStarted(activity: Activity) {}
    override fun onActivityStopped(activity: Activity) {}
    override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
    override fun onActivityDestroyed(activity: Activity) {}
}
