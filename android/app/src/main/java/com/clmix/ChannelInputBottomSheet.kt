package com.clmix

import android.content.Context
import android.content.res.ColorStateList
import android.os.SystemClock
import android.text.InputFilter
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.FrameLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.clmix.databinding.BottomSheetChannelInputBinding
import com.clmix.databinding.ChannelInputColumnBinding
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.android.material.R as MaterialR
import kotlin.math.abs

/**
 * A channel's input stage: its name, and its two input slots side by
 * side - Main and ALT - each with its own 48V and head-amp gain, plus the
 * digital trim behind them, each on a dial.
 *
 * Opened from the channel number above the name on the Full Mixer Control
 * strips. All of it is console-wide - a head amp feeds FOH, every monitor
 * and the recording at once - which is why it lives behind a sheet of its
 * own rather than on the strip, and why the dials have to be held to move
 * (see DialView).
 *
 * The Main / ALT buttons pick which slot feeds the channel. The ALT
 * column stays disabled until the console reports an alt route to switch
 * to (ChannelState.altAvailable). Trim sits after that switch on the
 * console, so there is only one: the Trim dial in each column is the same
 * value, and turning either moves both.
 */
class ChannelInputBottomSheet(
    context: Context,
    initial: ChannelState,
    private val gainRange: ClosedFloatingPointRange<Double>,
    private val trimRange: ClosedFloatingPointRange<Double>,
    private val onGainChanged: (Int, Double) -> Unit,
    private val onTrimChanged: (Int, Double) -> Unit,
    private val onPhantomChanged: (Int, Boolean) -> Unit,
    private val onAltGainChanged: (Int, Double) -> Unit,
    private val onAltPhantomChanged: (Int, Boolean) -> Unit,
    private val onAltInChanged: (Int, Boolean) -> Unit,
    private val onPhaseChanged: (Int, Int) -> Unit,
    private val onNameChanged: (Int, String) -> Unit
) {
    val channel: Int = initial.channel

    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetChannelInputBinding.inflate(dialog.layoutInflater)
    private val main: ChannelInputColumnBinding = binding.mainColumn
    private val alt: ChannelInputColumnBinding = binding.altColumn

    // One per value the console holds, not per dial: the two Trim dials
    // share a throttle, since they are one parameter on the desk.
    private val gainWrites = Throttle { onGainChanged(channel, it) }
    private val altGainWrites = Throttle { onAltGainChanged(channel, it) }
    private val trimWrites = Throttle { onTrimChanged(channel, it) }

    // The toggles - flipped on tap ahead of the console's echo, see
    // Optimistic.
    private val phantom = Optimistic(initial.phantom)
    private val altPhantom = Optimistic(initial.altPhantom)
    private val altIn = Optimistic(initial.altIn)

    // The polarity state being shown, as the console's own value rather
    // than a flag: 0 normal, 1..3 inverted (see ChannelState.phase).
    private val phase = Optimistic(initial.phase)

    // The inverted state to go back to when polarity is switched on
    // again. A stereo channel has three of them and this app cannot tell
    // them apart, so it remembers the one the desk reported instead of
    // assuming PHASE_INVERTED - otherwise tapping polarity off and on
    // would quietly move which leg is inverted.
    private var phaseLastInverted =
        if (initial.phase != PHASE_NORMAL) initial.phase else PHASE_INVERTED

    // A rename takes a moment to reach the console and come back. Until
    // it does, pushes still carry the old name, and writing that into the
    // box would undo what was just typed in front of the user.
    private val name = Optimistic(initial.name)

    private var altAvailable = initial.altAvailable

    init {
        dialog.setContentView(binding.root)

        // The name box is the first focusable thing on the sheet, and
        // would otherwise take focus - and the keyboard - the moment it
        // opens. The root holds focus instead until the box is tapped.
        binding.root.isFocusableInTouchMode = true
        binding.root.requestFocus()
        dialog.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_HIDDEN)

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

        binding.inputChannelNumber.text = "Channel $channel"
        setUpNameBox()

        main.sourceButton.text = "Main"
        alt.sourceButton.text = "ALT"

        main.sourceButton.setOnClickListener { selectSource(alt = false) }
        alt.sourceButton.setOnClickListener { selectSource(alt = true) }

        binding.phaseButton.setOnClickListener {
            val target = if (phase.shown != PHASE_NORMAL) {
                PHASE_NORMAL
            } else {
                phaseLastInverted
            }

            phase.set(target)
            applyPhase()
            onPhaseChanged(channel, target)
        }

        main.phantomButton.setOnClickListener {
            phantom.set(!phantom.shown)
            applyPhantom(main.phantomButton, phantom.shown)
            onPhantomChanged(channel, phantom.shown)
        }

        alt.phantomButton.setOnClickListener {
            altPhantom.set(!altPhantom.shown)
            applyPhantom(alt.phantomButton, altPhantom.shown)
            onAltPhantomChanged(channel, altPhantom.shown)
        }

        setUpDial(main.gainDial, main.gainValue, gainRange, initial.gain, gainWrites)
        setUpDial(alt.gainDial, alt.gainValue, gainRange, initial.altGain, altGainWrites)
        setUpDial(main.trimDial, main.trimValue, trimRange, initial.trim, trimWrites, mirror = alt)
        setUpDial(alt.trimDial, alt.trimValue, trimRange, initial.trim, trimWrites, mirror = main)

        applyPhantom(main.phantomButton, phantom.shown)
        applyPhantom(alt.phantomButton, altPhantom.shown)
        applyPhase()
        applySource()
        applyAltAvailable()
    }

    private fun setUpDial(
        dial: DialView,
        readout: TextView,
        range: ClosedFloatingPointRange<Double>,
        initial: Double?,
        writes: Throttle,
        mirror: ChannelInputColumnBinding? = null
    ) {
        dial.min = range.start
        dial.max = range.endInclusive
        dial.hasValue = initial != null
        // Nothing reported for this channel yet - the dial greys out and
        // the readout shows its floor rather than a number the console
        // never sent as if it had.
        dial.value = initial ?: range.start
        readout.text = formatDb(dial.value)

        dial.onValueChanged = { value ->
            readout.text = formatDb(value)
            mirrorTrim(mirror, value)
            writes.turned(value, force = false)
        }

        // Always sent, throttling or not: this is the value the operator
        // actually chose, and the console has to end up on it.
        dial.onTurnFinished = { value -> writes.turned(value, force = true) }
    }

    /** Keeps the other column's Trim dial on the one being turned. */
    private fun mirrorTrim(other: ChannelInputColumnBinding?, value: Double) {
        other ?: return
        other.trimDial.hasValue = true
        other.trimDial.value = value
        other.trimValue.text = formatDb(other.trimDial.value)
    }

    /**
     * The name box is edited in place: tap it and the current name is
     * selected, so typing replaces it (or it can be cleared and retyped),
     * and Done sends it. Closing the sheet mid-edit sends it too - what
     * was typed is what the operator meant.
     */
    private fun setUpNameBox() {
        val box = binding.inputChannelName

        box.setText(name.shown)
        box.filters = arrayOf(InputFilter.LengthFilter(MAX_NAME_LENGTH))

        box.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                finishRename()
                true
            } else {
                false
            }
        }

        box.setOnFocusChangeListener { _, focused -> if (!focused) commitName() }
    }

    private fun finishRename() {
        val box = binding.inputChannelName
        commitName()
        box.clearFocus()

        val keyboard = box.context.getSystemService(InputMethodManager::class.java)
        keyboard?.hideSoftInputFromWindow(box.windowToken, 0)
    }

    private fun commitName() {
        val box = binding.inputChannelName
        val typed = box.text.toString().trim()

        // An empty box is not a name - put back the one the desk has.
        if (typed.isEmpty() || typed == name.shown) {
            box.setText(name.shown)
            return
        }

        name.set(typed)
        box.setText(typed)
        onNameChanged(channel, typed)
    }

    private fun selectSource(alt: Boolean) {
        if (alt == altIn.shown) return
        if (alt && !altAvailable) return

        altIn.set(alt)
        applySource()
        onAltInChanged(channel, alt)
    }

    /**
     * Folds in a push from the server, unless that control has just been
     * changed by hand.
     */
    fun update(state: ChannelState) {
        if (state.channel != channel) return

        val box = binding.inputChannelName
        if (name.reconcile(state.name, NAME_CONFIRM_MS) && !box.hasFocus()) {
            box.setText(name.shown)
        }

        foldDial(main.gainDial, main.gainValue, state.gain, gainWrites)
        foldDial(alt.gainDial, alt.gainValue, state.altGain, altGainWrites)
        foldDial(main.trimDial, main.trimValue, state.trim, trimWrites)
        foldDial(alt.trimDial, alt.trimValue, state.trim, trimWrites)

        if (phantom.reconcile(state.phantom, TOGGLE_CONFIRM_MS)) {
            applyPhantom(main.phantomButton, phantom.shown)
        }

        if (altPhantom.reconcile(state.altPhantom, TOGGLE_CONFIRM_MS)) {
            applyPhantom(alt.phantomButton, altPhantom.shown)
        }

        if (phase.reconcile(state.phase, TOGGLE_CONFIRM_MS)) applyPhase()

        if (altIn.reconcile(state.altIn, TOGGLE_CONFIRM_MS)) applySource()

        if (state.altAvailable != altAvailable) {
            altAvailable = state.altAvailable
            applyAltAvailable()
        }
    }

    private fun foldDial(
        dial: DialView,
        readout: TextView,
        incoming: Double?,
        writes: Throttle
    ) {
        incoming ?: return

        if (writes.idle && (!dial.hasValue || abs(dial.value - incoming) >= 0.05)) {
            dial.hasValue = true
            dial.value = incoming
            readout.text = formatDb(dial.value)
        }
    }

    /**
     * 48V on reads as the console's own warning colour, not the app's
     * accent: it is the one control on this sheet that can damage a
     * source (ribbon mics especially), so it should look like a live
     * state rather than a selected option.
     */
    private fun applyPhantom(button: MaterialButton, on: Boolean) {
        val background = if (on) R.color.mute_active else R.color.mute_inactive
        val text = if (on) R.color.on_primary else R.color.on_mute_inactive

        tint(button, background, text)
        button.contentDescription = if (on) "48V on" else "48V off"
    }

    /** The live input's button fills with the accent; the other does not. */
    private fun applySource() {
        val onAlt = altIn.shown

        tint(
            main.sourceButton,
            if (onAlt) R.color.mute_inactive else R.color.primary,
            if (onAlt) R.color.on_mute_inactive else R.color.on_primary
        )
        tint(
            alt.sourceButton,
            if (onAlt) R.color.primary else R.color.mute_inactive,
            if (onAlt) R.color.on_primary else R.color.on_mute_inactive
        )
    }

    /** Greys out and locks the whole ALT column while there is no route. */
    private fun applyAltAvailable() {
        val enabled = altAvailable

        alt.root.alpha = if (enabled) 1f else DISABLED_ALPHA
        alt.sourceButton.isEnabled = enabled
        alt.phantomButton.isEnabled = enabled
        alt.gainDial.isEnabled = enabled
        alt.trimDial.isEnabled = enabled
    }

    /**
     * Polarity - the button fills with the app's accent when inverted
     * rather than the warning red 48V uses, since nothing here can damage
     * a microphone; it just sounds wrong.
     *
     * The state is the console's own number, and any non-zero value is
     * drawn the same way: a stereo channel's 1, 2 and 3 each invert
     * something, and which is which has never been established, so the
     * button says "inverted" and the number is remembered rather than
     * interpreted.
     */
    private fun applyPhase() {
        val state = phase.shown

        if (state != PHASE_NORMAL) phaseLastInverted = state

        val on = state != PHASE_NORMAL
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

    private fun tint(button: MaterialButton, background: Int, text: Int) {
        val context = button.context
        button.backgroundTintList =
            ColorStateList.valueOf(ContextCompat.getColor(context, background))
        button.setTextColor(ContextCompat.getColor(context, text))
    }

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener {
            if (binding.inputChannelName.hasFocus()) commitName()
            action()
        }
    }

    /**
     * Writes for one dial-driven value. A dial turns at the display's own
     * rate, and sending every one of those frames would put ~60 OSC writes
     * a second per dial on the console for a gesture the operator
     * experiences as one move - so writes are held to WRITE_INTERVAL_MS
     * while turning, and the value it settles on is always sent.
     *
     * It also remembers when the value was last turned by hand: a push
     * arriving right after a turn still carries the console's pre-turn
     * value (it has to travel to the desk and back), and writing that
     * into the dial would drag it backwards under the finger - so pushes
     * are ignored for SETTLE_MS after a turn, the same bargain the channel
     * strips strike for their faders.
     */
    private class Throttle(private val send: (Double) -> Unit) {
        private var touchedAt = 0L
        private var sentAt = 0L

        val idle: Boolean get() = SystemClock.uptimeMillis() - touchedAt > SETTLE_MS

        fun turned(value: Double, force: Boolean) {
            touchedAt = SystemClock.uptimeMillis()

            if (force || touchedAt - sentAt >= WRITE_INTERVAL_MS) {
                sentAt = touchedAt
                send(value)
            }
        }
    }

    /**
     * A value changed on tap rather than waiting for the console's echo -
     * the same bargain the strip's Mute button strikes. Pushes are ignored
     * until they agree or the confirm window passes, so a frame already in
     * flight cannot flip it back; after that the console's state wins.
     */
    private class Optimistic<T>(var shown: T) {
        private var expected: T? = null
        private var sentAt = 0L

        fun set(value: T) {
            expected = value
            sentAt = SystemClock.uptimeMillis()
            shown = value
        }

        /** Takes a pushed value; true if what is shown changed. */
        fun reconcile(incoming: T, confirmMs: Long): Boolean {
            if (expected != null &&
                (incoming == expected || SystemClock.uptimeMillis() - sentAt > confirmMs)
            ) {
                expected = null
            }

            if (expected != null || incoming == shown) return false

            shown = incoming
            return true
        }
    }

    companion object {
        // The dials' ranges are no longer constants here: each sweeps its
        // own parameter's range, which the server states at login and
        // HEAD_AMP_GAIN_RANGE / HEAD_AMP_TRIM_RANGE in MixerClient default
        // for. They used to share one span, the union -40..+60, which gave
        // gain 20 dB of travel the desk only ever clamps away.

        // How long after a turn to keep ignoring pushes for that dial.
        private const val SETTLE_MS = 700L

        // How long a tapped 48V, polarity or Main/ALT button holds its own
        // state before deferring to the console again.
        private const val TOGGLE_CONFIRM_MS = 2000L

        // A rename travels further than a flag - through the desktop's
        // cache and back out on the next push - so it is given longer.
        private const val NAME_CONFIRM_MS = 4000L

        // Matches the server's own cap (MAX_CHANNEL_NAME).
        private const val MAX_NAME_LENGTH = 32

        // Fast enough that the desk visibly tracks the dial, far below
        // the ~60 a second the turn itself generates.
        private const val WRITE_INTERVAL_MS = 50L

        private const val DISABLED_ALPHA = 0.4f

        fun formatDb(value: Double): String {
            val rounded = Math.round(value * 10.0) / 10.0
            return if (rounded > 0) "+%.1f".format(rounded) else "%.1f".format(rounded)
        }
    }
}
