package com.clmix

import android.os.Bundle
import android.widget.ArrayAdapter
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.core.widget.doAfterTextChanged
import com.clmix.databinding.ActivityFeedbackBinding
import kotlin.concurrent.thread

/**
 * The Feedback form, opened from the side menu: a category, a message
 * and an opt-in for diagnostics. Sending is Feedback.submit, which keeps
 * the message on the phone until it has gone - so this screen works the
 * same with or without the mixer server, and with or without internet.
 */
class FeedbackActivity : AppCompatActivity() {
    private lateinit var binding: ActivityFeedbackBinding
    private var sending = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        binding = ActivityFeedbackBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val bar = binding.topBar
        val barPadding = intArrayOf(bar.paddingLeft, bar.paddingTop, bar.paddingRight)
        val content = binding.content
        val contentPadding = intArrayOf(content.paddingLeft, content.paddingRight, content.paddingBottom)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            bar.setPadding(
                barPadding[0] + bars.left, barPadding[1] + bars.top,
                barPadding[2] + bars.right, bar.paddingBottom
            )
            content.setPadding(
                contentPadding[0] + bars.left, content.paddingTop,
                contentPadding[1] + bars.right,
                contentPadding[2] + maxOf(bars.bottom, ime.bottom)
            )
            insets
        }

        binding.closeButton.setOnClickListener { finish() }

        binding.categoryInput.setAdapter(
            ArrayAdapter(this, android.R.layout.simple_list_item_1, Feedback.CATEGORIES)
        )
        binding.categoryInput.setOnItemClickListener { _, _, _, _ -> onEdited() }
        binding.messageInput.doAfterTextChanged { onEdited() }

        binding.diagnosticsCheck.setOnCheckedChangeListener { _, checked ->
            binding.diagnosticsPreview.text = if (checked) Feedback.diagnostics(this) else ""
            binding.diagnosticsPreview.isVisible = checked
        }

        binding.sendButton.setOnClickListener { send() }
    }

    private fun category(): String? =
        binding.categoryInput.text?.toString()?.takeIf { it in Feedback.CATEGORIES }

    private fun message(): String = binding.messageInput.text?.toString()?.trim().orEmpty()

    private fun onEdited() {
        val length = message().length
        binding.messageLayout.helperText =
            if (length in 1 until Feedback.MIN_LENGTH) "At least ${Feedback.MIN_LENGTH} characters" else null
        binding.sendButton.isEnabled = !sending && category() != null &&
            length in Feedback.MIN_LENGTH..Feedback.MAX_LENGTH
    }

    private fun send() {
        val category = category() ?: return
        val message = message()
        val diagnostics = binding.diagnosticsCheck.isChecked
        sending = true
        onEdited()
        setStatus("Sending…", error = false)

        thread {
            val outcome = Feedback.submit(applicationContext, category, message, diagnostics)
            runOnUiThread {
                sending = false
                when (outcome) {
                    Feedback.Outcome.SENT, Feedback.Outcome.QUEUED -> {
                        setStatus(
                            if (outcome == Feedback.Outcome.SENT) "Thank you for your feedback."
                            else "Feedback saved and will be submitted automatically when internet becomes available.",
                            error = false
                        )
                        binding.messageInput.text = null
                        binding.diagnosticsCheck.isChecked = false
                    }
                    Feedback.Outcome.REJECTED ->
                        setStatus("Couldn't send that feedback. Please check it and try again.", error = true)
                }
                onEdited()
            }
        }
    }

    private fun setStatus(text: String, error: Boolean) {
        binding.statusLabel.text = text
        binding.statusLabel.setTextColor(
            ContextCompat.getColor(this, if (error) R.color.sos else R.color.on_surface_variant)
        )
        binding.statusLabel.isVisible = true
    }
}
