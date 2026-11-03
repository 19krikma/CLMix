package com.clmix

import android.content.Context
import android.view.ViewGroup
import android.widget.LinearLayout
import androidx.core.content.ContextCompat
import com.clmix.databinding.BottomSheetBankChannelsBinding
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.checkbox.MaterialCheckBox

/**
 * Every channel on the desk as a checkbox, ticked for the ones already
 * in [bankName]. Each tick is a toggle that applies straight away -
 * [onToggle] adds or removes that one channel - so the strips behind the
 * sheet change as you go, and Done only closes it.
 *
 * Listed in the console's order, whatever order the bank is in: this is
 * for finding a channel, and the desk's numbering is how you find one.
 */
class BankChannelsBottomSheet(
    context: Context,
    bankName: String,
    catalog: List<BankChannel>,
    inBank: Collection<Int>,
    private val onToggle: (channel: Int, checked: Boolean) -> Unit
) {
    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetBankChannelsBinding.inflate(dialog.layoutInflater)
    private val ticked = inBank.toMutableSet()
    private val total = catalog.size

    init {
        dialog.setContentView(binding.root)
        binding.bankChannelsTitle.text = "Channels in \"$bankName\""
        binding.bankChannelsDone.setOnClickListener { dialog.dismiss() }

        val density = context.resources.displayMetrics.density

        for (channel in catalog) {
            val box = MaterialCheckBox(context).apply {
                text = "${channel.channel}    ${channel.name}"
                textSize = 15f
                isChecked = channel.channel in ticked
                setTextColor(ContextCompat.getColor(context, R.color.on_surface))
                setPadding((8 * density).toInt(), 0, 0, 0)
                minHeight = (48 * density).toInt()
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT
                )

                setOnCheckedChangeListener { _, checked ->
                    if (checked) ticked.add(channel.channel) else ticked.remove(channel.channel)
                    updateCount()
                    onToggle(channel.channel, checked)
                }
            }

            binding.bankChannelsList.addView(box)
        }

        updateCount()

        // Most of the screen, so a full desk's worth of channels can be
        // scrolled through without dragging the sheet up first.
        val height = (context.resources.displayMetrics.heightPixels * 0.75f).toInt()
        binding.root.layoutParams = binding.root.layoutParams?.apply { this.height = height }
            ?: ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, height)
        binding.root.minimumHeight = height

        dialog.behavior.apply {
            peekHeight = height
            skipCollapsed = true
            state = BottomSheetBehavior.STATE_EXPANDED
        }
    }

    private fun updateCount() {
        binding.bankChannelsCount.text = "${ticked.size} of $total channels"
    }

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener { action() }
    }
}
