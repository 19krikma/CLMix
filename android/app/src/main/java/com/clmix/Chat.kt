package com.clmix

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
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
    /** The sender's username; null for the sound engineer's. */
    val user: String? = null,
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

    // Each sender's profile picture by username, fetched once and then
    // kept up to date by the server's "picture" pushes. A username
    // mapped to null has no picture; one missing hasn't been asked for.
    private val pictures = mutableMapOf<String, Bitmap?>()
    private val requested = mutableSetOf<String>()

    fun picture(user: String?): Bitmap? = user?.let { pictures[it] }

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
        fetchPictures()
    }

    fun onMessage(json: JSONObject) {
        val message = parse(json)
        if (messages.any { it.id == message.id }) return

        messages.add(message)
        if (messages.size > MAX_MESSAGES) messages.removeAt(0)
        fetchPictures()

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

    /** One account's picture, asked for or pushed after a change. */
    fun onPicture(json: JSONObject) {
        val user = json.optString("user").takeIf { it.isNotEmpty() } ?: return
        val encoded = json.optString("picture").takeIf { !json.isNull("picture") && it.isNotEmpty() }
        pictures[user] = encoded?.let {
            try {
                val bytes = Base64.decode(it, Base64.DEFAULT)
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            } catch (e: IllegalArgumentException) {
                null
            }
        }
        requested.add(user)
        onChange?.invoke(messages.toList())
    }

    /** Asks the server for the senders' pictures not yet known. Only a
     *  desktop with profiles has pictures to give. */
    private fun fetchPictures() {
        if (!Profile.supported) return
        val missing = messages.mapNotNull { it.user }.distinct().filter { it !in requested }
        if (missing.isEmpty()) return
        requested.addAll(missing)
        MixerClient.requestPictures(missing)
    }

    /** A different account may log in next, with its own "mine". */
    fun clear() {
        messages.clear()
        pictures.clear()
        requested.clear()
    }

    private fun parse(o: JSONObject) = ChatMessage(
        id = o.optLong("id"),
        kind = o.optString("kind", "user"),
        name = o.optString("name"),
        text = o.optString("text"),
        time = o.optDouble("time", 0.0),
        mine = o.optBoolean("mine", false),
        user = o.optString("user").takeIf { !o.isNull("user") && it.isNotEmpty() },
    )
}
