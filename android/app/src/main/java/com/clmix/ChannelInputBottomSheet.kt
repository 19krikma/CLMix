package com.clmix

import android.content.Context
import android.content.res.ColorStateList
import android.os.SystemClock
import android.text.InputFilter
import android.text.InputType
import android.widget.EditText
import android.widget.FrameLayout
import androidx.appcompat.app.AlertDialog
import androidx.core.content.ContextCompat
import com.clmix.databinding.BottomSheetChannelInputBinding
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.R as MaterialR
import kotlin.math.abs

/**
 * A channel's input stage: its name, which input feeds it, and the
 * head-amp gain and digital trim, each on a dial.
 *
 * Opened from the channel number above the name on the Full Mixer Control
 * strips. Both values are console-wide - a head amp feeds FOH, every
 * monitor and the recording at once - which is why they live behind a
 * sheet of their own rather than on the strip, and why the dials have to
 * be held to move (see DialView).
 */
class ChannelInputBottomSheet(
    context: Context,
    val channel: Int,
    channelName: String,
    gain: Double?,
    trim: Double?,
    phantom: Boolean,
    phase: Boolean,
    private val onGainChanged: (Int, Double) -> Unit,
    private val onTrimChanged: (Int, Double) -> Unit,
    private val onPhantomChanged: (Int, Boolean) -> Unit,
    private val onPhaseChanged: (Int, Boolean) -> Unit,
    private val onNameChanged: (Int, String) -> Unit,
    private val onInputClicked: (Int) -> Unit
) {
    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetChannelInputBinding.inflate(dialog.layoutInflater)

    // When each dial was last turned by hand. A push arriving right after
    // a turn still carries the console's pre-turn value (it has to travel
    // to the desk and back), and writing that into the dial would drag it
    // backwards under the finger - so pushes are ignored briefly after a
    // turn, the same bargain the channel strips strike for their faders.
    private var gainTouchedAt = 0L
    private var trimTouchedAt = 0L

    // When each dial last sent a write. A dial turns at the display's own
    // rate, and sending every one of those frames would put ~60 OSC
    // writes a second per dial on the console for a gesture the operator
    // experiences as one move. Held to WRITE_INTERVAL_MS while turning;
    // the value it settles on is always sent, so the console never ends
    // up on a rounded-off intermediate.
    private var gainSentAt = 0L
    private var trimSentAt = 0L

    // What the 48V button is showing. Flipped on tap rather than waiting
    // for the console's echo - the same bargain the strip's Mute button
    // strikes - and pushes are ignored until they agree or PHANTOM_
    // CONFIRM_MS passes, so a frame already in flight cannot flip it back.
    private var phantomShown = phantom
    private var phantomExpected: Boolean? = null
    private var phantomSentAt = 0L

    private var phaseShown = phase
    private var phaseExpected: Boolean? = null
    private var phaseSentAt = 0L

    // A rename takes a moment to reach the console and come back. Until
    // it does, pushes still carry the old name, and writing that into the
    // box would undo what was just typed in front of the user.
    private var nameExpected: String? = null
    private var nameSentAt = 0L

    init {
        dialog.setContentView(binding.root)

        // As PanBottomSheet: in landscape the default peek height cuts
        // the content off, so the sheet opens fully expanded.
        dialog.setOnShowListener {
            val sheet = dialog.findViewById<FrameLayout>(MaterialR.id.design_bottom_sheet)
            sheet?.let {
                val behavior = BottomSheetBehavior.from(it)
                behavior.state = BottomSheetBehavior.STATE_EXPANDED
                behavior.skipCollapsed = true
            }
        }

        binding.inputChannelName.text = channelName
        binding.inputChannelNumber.text = "Channel $channel"

        // Hold, not tap: renaming changes what every surface in the
        // building calls this channel.
        binding.inputChannelName.setOnLongClickListener {
            promptForName(binding.inputChannelName.text.toString())
            true
        }

        binding.inputButton.setOnClickListener { onInputClicked(channel) }

        applyPhantom(phantomShown)
        applyPhase(phaseShown)

        binding.phaseButton.setOnClickListener {
            val target = !phaseShown

            phaseExpected = target
            phaseSentAt = SystemClock.uptimeMillis()
            applyPhase(target)

            onPhaseChanged(channel, target)
        }

        binding.phantomButton.setOnClickListener {
            val target = !phantomShown

            phantomExpected = target
            phantomSentAt = SystemClock.uptimeMillis()
            applyPhantom(target)

            onPhantomChanged(channel, target)
        }

        setUpDial(
            dial = binding.gainDial,
            min = HEAD_AMP_MIN,
            max = HEAD_AMP_MAX,
            initial = gain,
            format = { binding.gainValue.text = formatDb(it) },
            onChanged = { value, force ->
                gainTouchedAt = SystemClock.uptimeMillis()

                if (force || gainTouchedAt - gainSentAt >= WRITE_INTERVAL_MS) {
                    gainSentAt = gainTouchedAt
                    onGainChanged(channel, value)
                }
            }
        )

        setUpDial(
            dial = binding.trimDial,
            min = HEAD_AMP_MIN,
            max = HEAD_AMP_MAX,
            initial = trim,
            format = { binding.trimValue.text = formatDb(it) },
            onChanged = { value, force ->
                trimTouchedAt = SystemClock.uptimeMillis()

                if (force || trimTouchedAt - trimSentAt >= WRITE_INTERVAL_MS) {
                    trimSentAt = trimTouchedAt
                    onTrimChanged(channel, value)
                }
            }
        )
    }

    private fun setUpDial(
        dial: DialView,
        min: Double,
        max: Double,
        initial: Double?,
        format: (Double) -> Unit,
        onChanged: (Double, Boolean) -> Unit
    ) {
        dial.min = min
        dial.max = max
        dial.hasValue = initial != null
        dial.value = initial ?: min

        format(dial.value)
        if (initial == null) dialPlaceholder(dial, format)

        dial.onValueChanged = { value ->
            format(value)
            onChanged(value, false)
        }

        // Always sent, throttling or not: this is the value the operator
        // actually chose, and the console has to end up on it.
        dial.onTurnFinished = { value -> onChanged(value, true) }
    }

    private fun dialPlaceholder(dial: DialView, format: (Double) -> Unit) {
        // Nothing reported for this channel yet - show a dash rather than
        // a number the console never sent.
        format(dial.value)
    }

    /**
     * Folds in a push from the server, unless that dial has just been
     * turned by hand.
     */
    fun update(state: ChannelState) {
        if (state.channel != channel) return

        val pendingName = nameExpected

        if (pendingName != null &&
            (state.name == pendingName ||
                SystemClock.uptimeMillis() - nameSentAt > NAME_CONFIRM_MS)
        ) {
            nameExpected = null
        }

        if (nameExpected == null && binding.inputChannelName.text != state.name) {
            binding.inputChannelName.text = state.name
        }

        state.gain?.let { gain ->
            if (idle(gainTouchedAt) && differs(binding.gainDial.value, gain)) {
                binding.gainDial.hasValue = true
                binding.gainDial.value = gain
                binding.gainValue.text = formatDb(binding.gainDial.value)
            }
        }

        state.trim?.let { trim ->
            if (idle(trimTouchedAt) && differs(binding.trimDial.value, trim)) {
                binding.trimDial.hasValue = true
                binding.trimDial.value = trim
                binding.trimValue.text = formatDb(binding.trimDial.value)
            }
        }

        val expected = phantomExpected

        if (expected != null) {
            // Settled once the console agrees, or given up on if it never
            // does - at which point the console's own state wins.
            if (state.phantom == expected ||
                SystemClock.uptimeMillis() - phantomSentAt > PHANTOM_CONFIRM_MS
            ) {
                phantomExpected = null
            }
        }

        if (phantomExpected == null && state.phantom != phantomShown) {
            applyPhantom(state.phantom)
        }

        val phaseWanted = phaseExpected

        if (phaseWanted != null) {
            if (state.phase == phaseWanted ||
                SystemClock.uptimeMillis() - phaseSentAt > PHANTOM_CONFIRM_MS
            ) {
                phaseExpected = null
            }
        }

        if (phaseExpected == null && state.phase != phaseShown) {
            applyPhase(state.phase)
        }
    }

    /**
     * 48V on reads as the console's own warning colour, not the app's
     * accent: it is the one control on this sheet that can damage a
     * source (ribbon mics especially), so it should look like a live
     * state rather than a selected option.
     */
    private fun applyPhantom(on: Boolean) {
        phantomShown = on

        val context = binding.phantomButton.context
        val background = if (on) R.color.mute_active else R.color.mute_inactive
        val text = if (on) R.color.on_primary else R.color.on_mute_inactive

        binding.phantomButton.backgroundTintList =
            ColorStateList.valueOf(ContextCompat.getColor(context, background))
        binding.phantomButton.setTextColor(ContextCompat.getColor(context, text))
        binding.phantomButton.contentDescription =
            if (on) "48V on" else "48V off"
    }

    /**
     * Polarity, which either is or is not inverted - so the button fills
     * with the app's accent when on rather than the warning red 48V uses.
     * Nothing here can damage a microphone; it just sounds wrong.
     */
    private fun applyPhase(on: Boolean) {
        phaseShown = on

        val context = binding.phaseButton.context
        val background = if (on) R.color.primary else R.color.mute_inactive
        val tint = if (on) R.color.on_primary else R.color.on_mute_inactive

        binding.phaseButton.backgroundTintList =
            ColorStateList.valueOf(ContextCompat.getColor(context, background))
        binding.phaseButton.iconTint =
            ColorStateList.valueOf(ContextCompat.getColor(context, tint))
        binding.phaseButton.contentDescription =
            if (on) "Polarity inverted" else "Polarity normal"
    }

    private fun promptForName(current: String) {
        val context = binding.root.context

        val field = EditText(context).apply {
            setText(current)
            setSelection(text.length)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS
            filters = arrayOf(InputFilter.LengthFilter(MAX_NAME_LENGTH))
        }

        // Padding so the field is not flush against the dialog's edges;
        // AlertDialog gives a custom view none of its own.
        val padding = (context.resources.displayMetrics.density * 20).toInt()
        val frame = FrameLayout(context).apply {
            setPadding(padding, padding / 2, padding, 0)
            addView(field)
        }

        AlertDialog.Builder(context)
            .setTitle("Rename channel $channel")
            .setView(frame)
            .setPositiveButton("Rename") { _, _ ->
                val name = field.text.toString().trim()

                if (name.isNotEmpty() && name != current) {
                    nameExpected = name
                    nameSentAt = SystemClock.uptimeMillis()
                    binding.inputChannelName.text = name
                    onNameChanged(channel, name)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun idle(touchedAt: Long): Boolean =
        SystemClock.uptimeMillis() - touchedAt > SETTLE_MS

    private fun differs(shown: Double, incoming: Double): Boolean =
        abs(shown - incoming) >= 0.05

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener { action() }
    }

    companion object {
        // One span for both head-amp dials. The console never states its
        // own limits over OSC, so these were read off the desk: the
        // official app sends unclamped dial positions and the desk pins
        // them, which makes whatever it reports back the range. Measured
        // 2026-09-20 - gain -20..+60, trim -40..+40 (see
        // docs/mixer_protocol/PROTOCOL.md, "Head-amp ranges").
        //
        // Deliberately the union of the two rather than a pair each, so
        // gain and trim read alike and neither dial can be short of a
        // value its parameter really holds. The cost is that each can be
        // turned into a region the desk will clamp - gain below -20,
        // trim above +40 - which is harmless here because a dial left
        // alone snaps back to whatever the console reports (see the
        // idle() checks in update()), so it corrects itself within
        // SETTLE_MS of letting go.
        //
        // Gain was 0..60 before this, which silently cost the bottom
        // 20 dB of the desk's range - a channel the console had at
        // -12 dB could not be dialled back to where it was.
        private const val HEAD_AMP_MIN = -40.0
        private const val HEAD_AMP_MAX = 60.0

        // How long after a turn to keep ignoring pushes for that dial.
        private const val SETTLE_MS = 700L

        // How long a tapped 48V or polarity button holds its own state
        // before deferring to the console again.
        private const val PHANTOM_CONFIRM_MS = 2000L

        // A rename travels further than a flag - through the desktop's
        // cache and back out on the next push - so it is given longer.
        private const val NAME_CONFIRM_MS = 4000L

        // Matches the server's own cap (MAX_CHANNEL_NAME).
        private const val MAX_NAME_LENGTH = 32

        // Fast enough that the desk visibly tracks the dial, far below
        // the ~60 a second the turn itself generates.
        private const val WRITE_INTERVAL_MS = 50L

        fun formatDb(value: Double): String {
            val rounded = Math.round(value * 10.0) / 10.0
            return if (rounded > 0) "+%.1f".format(rounded) else "%.1f".format(rounded)
        }
    }
}
