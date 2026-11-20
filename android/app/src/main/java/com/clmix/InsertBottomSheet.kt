package com.clmix

import android.content.Context
import android.content.res.ColorStateList
import android.text.InputType
import android.text.method.DigitsKeyListener
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import androidx.core.content.ContextCompat
import com.clmix.ChannelInputBottomSheet.Companion.formatDb
import com.clmix.databinding.BottomSheetInsertBinding
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.android.material.R as MaterialR
import kotlin.math.abs

/**
 * One of a channel's insert points, A or B (DiGiCo's Send A / Send B) -
 * whichever INS button on a Full Mixer Control strip was tapped. Each has
 * its own sheet: A and B are separate settings on the console, so they
 * are kept apart here too.
 *
 * Each column: the insert's in/out, the return's 48V and pad, and its
 * head-amp gain on a dial with a box that can be typed into - the same
 * furniture, and the same rules, as the channel input sheet (see
 * ChannelInputBottomSheet): toggles flip on tap ahead of the console's
 * echo, dial writes are throttled while turning, and pushes are ignored
 * for a moment after a turn.
 *
 * All of it is console-wide, like the input stage: an insert sits in the
 * channel, so it changes what every mix hears.
 */
class InsertBottomSheet(
    context: Context,
    initial: ChannelState,
    /** "A" or "B" - the insert this sheet is for. */
    val slot: String,
    /** (channel, "A"/"B", "in"/"phantom"/"pad"/"gain", value) */
    private val onChanged: (Int, String, String, Any) -> Unit,
) {
    val channel: Int = initial.channel

    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetInsertBinding.inflate(dialog.layoutInflater)
    // The one insert this sheet shows.
    private val columns = listOf(
        Column(slot, binding, if (slot == "A") initial.insertA else initial.insertB)
    )

    private inner class Column(val slot: String, val views: BottomSheetInsertBinding, initial: InsertState?) {
        val inOn = ChannelInputBottomSheet.Optimistic(initial?.inOn ?: false)
        val phantom = ChannelInputBottomSheet.Optimistic(initial?.phantom ?: false)
        val pad = ChannelInputBottomSheet.Optimistic(initial?.pad ?: false)
        val gainWrites = ChannelInputBottomSheet.Throttle { onChanged(channel, slot, "gain", it) }
    }

    init {
        dialog.setContentView(binding.root)
        binding.root.isFocusableInTouchMode = true
        binding.root.requestFocus()
        dialog.window?.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_HIDDEN)

        dialog.setOnShowListener {
            dialog.findViewById<FrameLayout>(MaterialR.id.design_bottom_sheet)?.let {
                val behavior = BottomSheetBehavior.from(it)
                behavior.state = BottomSheetBehavior.STATE_EXPANDED
                behavior.skipCollapsed = true
            }
        }

        binding.insertChannelNumber.text = "Channel $channel"
        binding.insertChannelName.text = initial.name

        for (column in columns) {
            val views = column.views

            views.inButton.text = "Insert ${column.slot}"
            views.inButton.setOnClickListener {
                column.inOn.set(!column.inOn.shown)
                applyIn(column)
                onChanged(channel, column.slot, "in", column.inOn.shown)
            }
            views.phantomButton.setOnClickListener {
                column.phantom.set(!column.phantom.shown)
                applyPhantom(views.phantomButton, column.phantom.shown)
                onChanged(channel, column.slot, "phantom", column.phantom.shown)
            }
            views.padButton.setOnClickListener {
                column.pad.set(!column.pad.shown)
                applyPad(views.padButton, column.pad.shown)
                onChanged(channel, column.slot, "pad", column.pad.shown)
            }

            val gain = if (column.slot == "A") initial.insertA?.gain else initial.insertB?.gain
            setUpGain(column, gain)

            applyIn(column)
            applyPhantom(views.phantomButton, column.phantom.shown)
            applyPad(views.padButton, column.pad.shown)
        }
    }

    private fun setUpGain(column: Column, initial: Double?) {
        val dial = column.views.gainDial
        val box = column.views.gainValue

        dial.min = GAIN_RANGE.start
        dial.max = GAIN_RANGE.endInclusive
        // A head amp: the same quarter-speed turn as the input sheet's.
        dial.rate = 0.25
        dial.hasValue = initial != null
        dial.value = initial ?: GAIN_RANGE.start
        box.setText(formatDb(dial.value))

        dial.onValueChanged = { value ->
            box.setText(formatDb(value))
            column.gainWrites.turned(value, force = false)
        }
        dial.onTurnFinished = { value -> column.gainWrites.turned(value, force = true) }

        box.keyListener = DigitsKeyListener.getInstance("0123456789+-.,")
        box.setRawInputType(
            InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_SIGNED or
                InputType.TYPE_NUMBER_FLAG_DECIMAL
        )
        box.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                hideKeyboard(box)
                true
            } else {
                false
            }
        }
        box.setOnFocusChangeListener { _, focused -> if (!focused) commitGain(column) }
    }

    private fun commitGain(column: Column) {
        val dial = column.views.gainDial
        val box = column.views.gainValue
        val typed = box.text.toString().trim().replace(',', '.').toDoubleOrNull()

        if (typed == null) {
            box.setText(formatDb(dial.value))
            return
        }

        dial.hasValue = true
        dial.value = typed.coerceIn(dial.min, dial.max)
        box.setText(formatDb(dial.value))
        column.gainWrites.turned(dial.value, force = true)
    }

    private fun hideKeyboard(box: EditText) {
        box.clearFocus()
        box.context.getSystemService(InputMethodManager::class.java)
            ?.hideSoftInputFromWindow(box.windowToken, 0)
    }

    /** Folds in a push from the server, unless a control was just worked. */
    fun update(state: ChannelState) {
        if (state.channel != channel) return
        binding.insertChannelName.text = state.name

        for (column in columns) {
            val insert = (if (column.slot == "A") state.insertA else state.insertB) ?: continue
            val views = column.views

            insert.inOn?.let { if (column.inOn.reconcile(it, TOGGLE_CONFIRM_MS)) applyIn(column) }
            insert.phantom?.let {
                if (column.phantom.reconcile(it, TOGGLE_CONFIRM_MS)) {
                    applyPhantom(views.phantomButton, column.phantom.shown)
                }
            }
            insert.pad?.let {
                if (column.pad.reconcile(it, TOGGLE_CONFIRM_MS)) applyPad(views.padButton, column.pad.shown)
            }

            val gain = insert.gain ?: continue
            val dial = views.gainDial
            if (column.gainWrites.idle && (!dial.hasValue || abs(dial.value - gain) >= 0.05)) {
                dial.hasValue = true
                dial.value = gain
                if (!views.gainValue.hasFocus()) views.gainValue.setText(formatDb(dial.value))
            }
        }
    }

    /** In: the accent fill, as the live Main/ALT button. Out: grey. */
    private fun applyIn(column: Column) {
        val on = column.inOn.shown
        tint(
            column.views.inButton,
            if (on) R.color.primary else R.color.mute_inactive,
            if (on) R.color.on_primary else R.color.on_mute_inactive
        )
        column.views.inButton.contentDescription =
            "Insert ${column.slot} ${if (on) "in" else "out"}"
    }

    /** 48V in the console's warning colour, as on the input sheet. */
    private fun applyPhantom(button: MaterialButton, on: Boolean) {
        tint(
            button,
            if (on) R.color.mute_active else R.color.mute_inactive,
            if (on) R.color.on_primary else R.color.on_mute_inactive
        )
        button.contentDescription = if (on) "48V on" else "48V off"
    }

    private fun applyPad(button: MaterialButton, on: Boolean) {
        tint(
            button,
            if (on) R.color.primary else R.color.mute_inactive,
            if (on) R.color.on_primary else R.color.on_mute_inactive
        )
        button.contentDescription = if (on) "Pad on" else "Pad off"
    }

    private fun tint(button: MaterialButton, background: Int, text: Int) {
        val context = button.context
        button.backgroundTintList = ColorStateList.valueOf(ContextCompat.getColor(context, background))
        button.setTextColor(ContextCompat.getColor(context, text))
    }

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener {
            // A gain typed but not committed is what the operator meant.
            columns.forEach { if (it.views.gainValue.hasFocus()) commitGain(it) }
            action()
        }
    }

    companion object {
        // The insert return's head amp, as the console's dictionary has it.
        private val GAIN_RANGE = -20.0..60.0
        private const val TOGGLE_CONFIRM_MS = 2000L
    }
}
