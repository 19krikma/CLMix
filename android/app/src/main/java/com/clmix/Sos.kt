package com.clmix

import android.app.Activity
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.widget.Toast
import com.google.android.material.dialog.MaterialAlertDialogBuilder

/**
 * The SOS button on the AUX mixer screen (MixerActivity's top bar, and its
 * landscape sidebar). It offers DEAD BATTERY, AUX NOT WORKING, NEED
 * ASSISTANCE or CANCEL; the first three go to the server
 * ({"action": "sos", "reason": ...}), which pops an SOS window up on the
 * sound engineer's desktop saying what is needed and who asked.
 *
 * App-wide rather than the screen's own so the reply - which MixerClient
 * hands here rather than to the screen listener - is still reported if
 * the screen was rotated or left in the meantime.
 */
object Sos {
    // Wire values, as services/remote_server.py's SOS_REASONS knows them,
    // with the dialog_sos button that sends each.
    private val REASONS = listOf(
        "dead_battery" to R.id.sos_dead_battery,
        "aux_not_working" to R.id.sos_aux_not_working,
        "need_assistance" to R.id.sos_need_assistance,
    )

    // No answer by then and the SOS is reported as not delivered: the
    // connection is down, or the desktop is too old to know SOS.
    private const val REPLY_TIMEOUT_MS = 6000L

    private var appContext: Context? = null
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

    fun showOptions(activity: Activity) {
        if (activity.isFinishing) return

        // A custom view rather than setItems, whose rows are plain text
        // that does not read as tappable: each choice is a filled button.
        val view = LayoutInflater.from(activity).inflate(R.layout.dialog_sos, null)
        val dialog = MaterialAlertDialogBuilder(activity)
            .setView(view)
            .create()

        REASONS.forEach { (reason, buttonId) ->
            view.findViewById<View>(buttonId).setOnClickListener {
                dialog.dismiss()
                send(reason)
            }
        }
        view.findViewById<View>(R.id.sos_cancel).setOnClickListener { dialog.dismiss() }

        dialog.show()
    }

    private fun send(reason: String) {
        pending = true
        handler.removeCallbacks(timeout)
        handler.postDelayed(timeout, REPLY_TIMEOUT_MS)
        MixerClient.sendSos(reason)
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

    private fun toast(text: String) {
        val context = appContext ?: return
        Toast.makeText(context, text, Toast.LENGTH_LONG).show()
    }
}
