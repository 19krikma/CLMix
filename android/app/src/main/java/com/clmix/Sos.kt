package com.clmix

import android.app.Activity
import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.widget.LinearLayout
import android.widget.Toast
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import org.json.JSONArray

/**
 * The SOS button on the AUX mixer screen (MixerActivity's top bar, and its
 * landscape sidebar). It offers the messages the server sent - set up in
 * the desktop's Settings > General, and able to differ per snapshot - or
 * CANCEL. A message goes to the server ({"action": "sos", "reason": id}),
 * which pops an SOS window up on the sound engineer's desktop saying what
 * is needed and who asked.
 *
 * App-wide rather than the screen's own so the reply - which MixerClient
 * hands here rather than to the screen listener - is still reported if
 * the screen was rotated or left in the meantime.
 */
object Sos {
    /** One SOS choice: the id the server knows it by, its text, and the
     *  button colour. */
    data class Option(val id: String, val text: String, val color: Int)

    private val SOS_RED = Color.parseColor("#C62828")

    // What a desktop from before the list could be edited offers, by the
    // ids it knows - see services/sos_options.py's DEFAULT_OPTIONS.
    private val BUILT_IN = listOf(
        Option("dead_battery", "DEAD BATTERY", SOS_RED),
        Option("aux_not_working", "AUX NOT WORKING", SOS_RED),
        Option("need_assistance", "NEED ASSISTANCE", SOS_RED),
    )

    private var options: List<Option> = BUILT_IN

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

        val list = view.findViewById<LinearLayout>(R.id.sos_options)
        val inflater = LayoutInflater.from(activity)
        options.forEach { option ->
            val button = inflater.inflate(R.layout.item_sos_option, list, false)
                as MaterialButton
            button.text = option.text
            button.backgroundTintList = ColorStateList.valueOf(option.color)
            button.setTextColor(textColorFor(option.color))
            button.setOnClickListener {
                dialog.dismiss()
                send(option.id)
            }
            list.addView(button)
        }
        view.findViewById<View>(R.id.sos_none).visibility =
            if (options.isEmpty()) View.VISIBLE else View.GONE
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

    /** The server's list, from login_result or a later "sos_options".
     *  Null - a desktop too old to send one - keeps the built-in three. */
    fun onOptions(array: JSONArray?) {
        if (array == null) {
            options = BUILT_IN
            return
        }

        options = (0 until array.length()).mapNotNull { i ->
            val item = array.optJSONObject(i) ?: return@mapNotNull null
            val id = item.optString("id").takeIf { it.isNotEmpty() }
                ?: return@mapNotNull null
            val text = item.optString("text").takeIf { it.isNotEmpty() }
                ?: return@mapNotNull null
            val color = try {
                Color.parseColor(item.optString("color"))
            } catch (e: IllegalArgumentException) {
                SOS_RED
            }
            Option(id, text, color)
        }
    }

    /** White or black, whichever reads on this button colour - the same
     *  rule as the desktop's preview (ui/sos_section.py). */
    private fun textColorFor(color: Int): Int {
        val luminance = 0.299 * Color.red(color) + 0.587 * Color.green(color) +
            0.114 * Color.blue(color)
        return if (luminance > 150) Color.BLACK else Color.WHITE
    }

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
