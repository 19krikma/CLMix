package com.clmix

import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.clmix.databinding.ActivityChatBinding
import java.text.DateFormat
import java.util.Date

/**
 * The chat every logged-in phone shares with the sound engineer: anyone
 * can post, everyone sees it. Opened from the aux mixer's menu.
 *
 * Messages are signed with the account's name; the desk's own come in
 * as "Sound Tech" on a bubble of their own colour, and every SOS shows
 * up here too, in red. The history lives in Chat, which keeps taking
 * messages while this screen is closed.
 */
class ChatActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityChatBinding
    private val adapter = MessageAdapter()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val bar = binding.topBar
        val barPadding = intArrayOf(bar.paddingLeft, bar.paddingTop, bar.paddingRight)
        val input = binding.inputBar
        val inputPadding = intArrayOf(input.paddingLeft, input.paddingBottom, input.paddingRight)
        val messagePadding = binding.messageList.paddingLeft
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            // The keyboard, when up, takes the place of the nav bar.
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            bar.setPadding(
                barPadding[0] + bars.left, barPadding[1] + bars.top,
                barPadding[2] + bars.right, bar.paddingBottom
            )
            input.setPadding(
                inputPadding[0] + bars.left, input.paddingTop,
                inputPadding[2] + bars.right, inputPadding[1] + maxOf(bars.bottom, ime.bottom)
            )
            binding.messageList.setPadding(
                bars.left + messagePadding, binding.messageList.paddingTop,
                bars.right + messagePadding, binding.messageList.paddingBottom
            )
            insets
        }

        binding.messageList.layoutManager = LinearLayoutManager(this).apply {
            stackFromEnd = true
        }
        binding.messageList.adapter = adapter
        // Keep the newest message in view when the keyboard squeezes
        // the list.
        binding.messageList.addOnLayoutChangeListener { _, _, _, _, bottom, _, _, _, oldBottom ->
            if (bottom < oldBottom) scrollToEnd()
        }

        binding.closeButton.setOnClickListener { finish() }
        binding.sendButton.setOnClickListener { send() }
        binding.messageInput.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                send()
                true
            } else {
                false
            }
        }
    }

    override fun onResume() {
        super.onResume()

        if (!MixerClient.isConnected) {
            finish()
            return
        }

        MixerClient.claimListener(this)
        Chat.onChange = ::show
        MixerClient.requestChatHistory()
    }

    override fun onPause() {
        super.onPause()
        Chat.onChange = null
        MixerClient.releaseListener(this)
    }

    override fun onDisconnected() {
        finish()
    }

    private fun send() {
        val text = binding.messageInput.text.toString().trim()
        if (text.isEmpty()) return
        MixerClient.sendChat(text)
        binding.messageInput.setText("")
    }

    private fun show(messages: List<ChatMessage>) {
        val atEnd = !binding.messageList.canScrollVertically(1)
        val grew = messages.size > adapter.itemCount
        adapter.submit(messages)
        binding.emptyLabel.isVisible = messages.isEmpty()

        // Follow new messages, unless the reader has scrolled up through
        // the history - but always for one this phone just sent.
        if (grew && (atEnd || messages.last().mine)) scrollToEnd()
    }

    private fun scrollToEnd() {
        if (adapter.itemCount > 0) {
            binding.messageList.scrollToPosition(adapter.itemCount - 1)
        }
    }

    private inner class MessageAdapter : RecyclerView.Adapter<MessageAdapter.Holder>() {
        private var items: List<ChatMessage> = emptyList()
        private val timeFormat = DateFormat.getTimeInstance(DateFormat.SHORT)
        // Lazy: the adapter is built with the activity, before it has resources.
        private val density by lazy { resources.displayMetrics.density }

        fun submit(messages: List<ChatMessage>) {
            items = messages
            @Suppress("NotifyDataSetChanged")
            notifyDataSetChanged()
        }

        inner class Holder(view: View) : RecyclerView.ViewHolder(view) {
            val bubble: LinearLayout = view.findViewById(R.id.bubble)
            val name: TextView = view.findViewById(R.id.name)
            val time: TextView = view.findViewById(R.id.time)
            val text: TextView = view.findViewById(R.id.text)
        }

        override fun getItemCount() = items.size

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = Holder(
            LayoutInflater.from(parent.context).inflate(R.layout.item_chat_message, parent, false)
        )

        override fun onBindViewHolder(holder: Holder, position: Int) {
            val message = items[position]
            val context = holder.itemView.context

            val (fill, ink) = when {
                message.isSos -> R.color.sos to android.R.color.white
                message.isTech -> R.color.chat_tech to R.color.on_chat_tech
                message.mine -> R.color.chat_mine to R.color.on_surface
                else -> R.color.chat_other to R.color.on_surface
            }
            val inkColor = ContextCompat.getColor(context, ink)

            holder.bubble.background = GradientDrawable().apply {
                cornerRadius = 16 * density
                setColor(ContextCompat.getColor(context, fill))
            }
            (holder.bubble.layoutParams as FrameLayout.LayoutParams).gravity =
                if (message.mine) Gravity.END else Gravity.START

            // Most of the width, so a long message still reads as a
            // bubble on its own side.
            val maxWidth = (binding.messageList.width * 0.8f).toInt()
                .takeIf { it > 0 } ?: (resources.displayMetrics.widthPixels * 0.75f).toInt()
            holder.text.maxWidth = maxWidth
            holder.name.maxWidth = maxWidth

            holder.name.text = message.name
            holder.time.text = timeFormat.format(Date((message.time * 1000).toLong()))
            holder.text.text = message.text
            holder.name.setTextColor(inkColor)
            holder.time.setTextColor(inkColor)
            holder.text.setTextColor(inkColor)
        }
    }
}
