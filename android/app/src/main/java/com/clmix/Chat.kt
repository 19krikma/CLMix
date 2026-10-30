package com.clmix

import android.content.Context
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject

/** One line of the shared chat - see services/chat_log.py. */
data class ChatMessage(
    val id: Long,
    /** "user", "tech" (the sound engineer at the desktop) or "sos". */
    val kind: String,
    val name: String,
    val text: String,
    /** Seconds since the epoch, as the server stamped it. */
    val time: Double,
    /** Written by this phone's own account. */
    val mine: Boolean,
) {
    val isTech get() = kind == "tech"
    val isSos get() = kind == "sos"
}

/**
 * The chat every logged-in phone shares with the sound engineer's desk.
 *
 * App-wide, like Sos, rather than ChatActivity's own: messages keep
 * arriving while the chat is closed, and a message from the sound
 * engineer is worth a toast wherever the phone is. MixerClient hands
 * "chat" and "chat_history" straight here.
 */
object Chat {
    private const val MAX_MESSAGES = 300

    private var appContext: Context? = null
    private val messages = mutableListOf<ChatMessage>()

    /** The open chat screen's redraw; null while it is closed. */
    var onChange: ((List<ChatMessage>) -> Unit)? = null
        set(value) {
            field = value
            value?.invoke(messages.toList())
        }

    fun init(context: Context) {
        appContext = context.applicationContext
    }

    // --- From MixerClient, on the main thread ---

    fun onHistory(items: JSONArray) {
        messages.clear()
        for (i in 0 until items.length()) {
            items.optJSONObject(i)?.let { messages.add(parse(it)) }
        }
        onChange?.invoke(messages.toList())
    }

    fun onMessage(json: JSONObject) {
        val message = parse(json)
        if (messages.any { it.id == message.id }) return

        messages.add(message)
        if (messages.size > MAX_MESSAGES) messages.removeAt(0)

        val screen = onChange
        if (screen != null) {
            screen(messages.toList())
        } else if (message.isTech) {
            // Chat closed: the sound engineer's words still reach the
            // performer, on whatever screen they are on.
            appContext?.let {
                Toast.makeText(it, "Sound Tech: ${message.text}", Toast.LENGTH_LONG).show()
            }
        }
    }

    /** A different account may log in next, with its own "mine". */
    fun clear() {
        messages.clear()
    }

    private fun parse(o: JSONObject) = ChatMessage(
        id = o.optLong("id"),
        kind = o.optString("kind", "user"),
        name = o.optString("name"),
        text = o.optString("text"),
        time = o.optDouble("time", 0.0),
        mine = o.optBoolean("mine", false),
    )
}
