package com.clmix

import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.widget.CheckBox
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import com.clmix.databinding.ActivityCustomBanksBinding
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText

/**
 * This account's own banks: which ones exist, and which of the desk's
 * channels are under each.
 *
 * Only reachable from the aux mixer's drawer, and only for an account
 * holding personalization - the same permission as personal channel
 * names, because this is the same kind of thing: one account's view of a
 * console everyone else is sharing. Nothing here reaches the desk, and
 * no other phone sees any of it.
 *
 * The server owns the set. This screen never edits in place and hopes:
 * every change sends the whole set and redraws from the reply, so what
 * is on screen is always what was actually stored - including the
 * server's own cleaning up of it (see UserStore._clean_banks).
 */
class CustomBanksActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityCustomBanksBinding

    // Last set the server sent, and the console's channels to build them
    // from. Both replaced wholesale by every onCustomBanks.
    private var banks: List<CustomBank> = emptyList()
    private var channels: List<BankChannel> = emptyList()

    // Which bank's channels the lower list is showing. By name rather
    // than index, so a set that comes back reordered or shorter does not
    // silently select a different bank than the one that was selected.
    private var selectedName: String? = null

    // The bank being edited, and the ticks so far. Held here rather than
    // read back off the checkboxes so that cancelling is free.
    private var editingName: String? = null
    private val editingChannels = mutableSetOf<Int>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        binding = ActivityCustomBanksBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val titleBaseTop = binding.screenTitle.paddingTop
        val editTitleBaseTop = binding.editTitle.paddingTop
        val rootBaseBottom = binding.root.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.screenTitle.setPadding(
                binding.screenTitle.paddingLeft, titleBaseTop + bars.top,
                binding.screenTitle.paddingRight, binding.screenTitle.paddingBottom
            )
            binding.editTitle.setPadding(
                binding.editTitle.paddingLeft, editTitleBaseTop + bars.top,
                binding.editTitle.paddingRight, binding.editTitle.paddingBottom
            )
            view.setPadding(
                view.paddingLeft, view.paddingTop,
                view.paddingRight, rootBaseBottom + bars.bottom
            )
            insets
        }

        binding.addButton.setOnClickListener { promptForNewBank() }
        binding.editButton.setOnClickListener { startEditing() }
        binding.removeButton.setOnClickListener { confirmRemove() }
        binding.resetButton.setOnClickListener { confirmReset() }
        binding.doneButton.setOnClickListener { finish() }

        binding.cancelEditButton.setOnClickListener { showBrowse() }
        binding.saveEditButton.setOnClickListener { saveEdit() }

        // Back out of an edit rather than out of the screen, so a
        // half-finished bank is not lost to a stray swipe.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (binding.editView.isVisible) showBrowse() else finish()
            }
        })

        render()
    }

    override fun onResume() {
        super.onResume()

        // The socket can die while this screen is up, which would leave
        // an editor saving into nothing.
        if (!MixerClient.isConnected) {
            finish()
            return
        }

        MixerClient.claimListener(this)
        MixerClient.requestCustomBanks()
    }

    override fun onPause() {
        super.onPause()
        MixerClient.releaseListener(this)
    }

    override fun onDisconnected() {
        finish()
    }

    override fun onCustomBanks(banks: List<CustomBank>, channels: List<BankChannel>) {
        this.banks = banks
        this.channels = channels

        if (banks.none { it.name == selectedName }) {
            selectedName = banks.firstOrNull()?.name
        }

        render()
    }

    override fun onError(message: String) {
        AlertDialog.Builder(this)
            .setMessage(message)
            .setPositiveButton("OK", null)
            .show()
    }

    // MARK: - Browsing

    private fun render() {
        binding.bankList.removeAllViews()

        for (bank in banks) {
            binding.bankList.addView(bankRow(bank))
        }

        binding.emptyLabel.isVisible = banks.isEmpty()

        val selected = banks.firstOrNull { it.name == selectedName }
        binding.editButton.isEnabled = selected != null
        binding.removeButton.isEnabled = selected != null

        binding.channelsHeader.isVisible = selected != null
        binding.channelSummary.removeAllViews()

        if (selected == null) return

        binding.channelsHeader.text =
            "${selected.channels.size} channels in \"${selected.name}\""

        if (selected.channels.isEmpty()) {
            binding.channelSummary.addView(
                summaryRow("Nothing in this bank yet - tap Edit to pick channels.")
            )
            return
        }

        // In the console's order rather than the order they were ticked,
        // which is how every other channel list in the app reads.
        for (channel in selected.channels.sorted()) {
            binding.channelSummary.addView(summaryRow("$channel   ${nameFor(channel)}"))
        }
    }

    private fun bankRow(bank: CustomBank): View {
        val selected = bank.name == selectedName

        return MaterialButton(this).apply {
            text = "${bank.name}  (${bank.channels.size})"
            isAllCaps = false
            cornerRadius = dpToPx(10)
            layoutParams = android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.MATCH_PARENT,
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dpToPx(8) }

            // The same selected/unselected pair the discovered server
            // rows use on the connect screen, so "the one I picked"
            // looks the same everywhere in the app.
            if (selected) {
                strokeWidth = 0
                backgroundTintList = ColorStateList.valueOf(
                    ContextCompat.getColor(context, R.color.primary)
                )
                setTextColor(ContextCompat.getColor(context, R.color.on_primary))
            } else {
                strokeWidth = dpToPx(1)
                strokeColor = ColorStateList.valueOf(
                    ContextCompat.getColor(context, R.color.outline)
                )
                backgroundTintList = ColorStateList.valueOf(Color.TRANSPARENT)
                setTextColor(ContextCompat.getColor(context, R.color.on_surface))
            }

            setOnClickListener {
                selectedName = bank.name
                render()
            }
        }
    }

    private fun summaryRow(text: String): View = TextView(this).apply {
        this.text = text
        textSize = 14f
        setTextColor(ContextCompat.getColor(context, R.color.on_surface))
        setPadding(0, dpToPx(6), 0, dpToPx(6))
    }

    private fun nameFor(channel: Int): String =
        channels.firstOrNull { it.channel == channel }?.name ?: "Ch $channel"

    // MARK: - Add, remove, reset

    private fun promptForNewBank() {
        val field = TextInputEditText(this).apply {
            hint = "Bank name"
            setPadding(dpToPx(20), dpToPx(16), dpToPx(20), dpToPx(16))
        }

        AlertDialog.Builder(this)
            .setTitle("New bank")
            .setView(field)
            .setPositiveButton("Add") { _, _ ->
                val name = field.text?.toString()?.trim().orEmpty()

                if (name.isEmpty()) return@setPositiveButton

                if (banks.any { it.name.equals(name, ignoreCase = true) }) {
                    onError("There is already a bank called \"$name\".")
                    return@setPositiveButton
                }

                // Created empty and selected, so the obvious next tap is
                // Edit - rather than throwing the user into a channel
                // list they did not ask for yet.
                selectedName = name
                MixerClient.saveCustomBanks(banks + CustomBank(name, emptyList()))
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun confirmRemove() {
        val selected = banks.firstOrNull { it.name == selectedName } ?: return

        AlertDialog.Builder(this)
            .setTitle("Remove \"${selected.name}\"?")
            .setMessage("The channels stay on the desk - only your grouping goes.")
            .setPositiveButton("Remove") { _, _ ->
                selectedName = null
                MixerClient.saveCustomBanks(banks.filter { it.name != selected.name })
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun confirmReset() {
        AlertDialog.Builder(this)
            .setTitle("Reset to the mixer's banks?")
            .setMessage("Your banks are replaced by the ones the console reports.")
            .setPositiveButton("Reset") { _, _ ->
                selectedName = null
                MixerClient.resetCustomBanks()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // MARK: - Editing one bank

    private fun startEditing() {
        val selected = banks.firstOrNull { it.name == selectedName } ?: return

        editingName = selected.name
        editingChannels.clear()
        editingChannels.addAll(selected.channels)

        binding.editTitle.text = "Edit \"${selected.name}\""
        binding.editName.setText(selected.name)
        binding.editChannelList.removeAllViews()

        for (channel in channels) {
            binding.editChannelList.addView(channelCheckbox(channel))
        }

        updateEditCount()
        binding.browseView.isVisible = false
        binding.editView.isVisible = true
    }

    private fun channelCheckbox(channel: BankChannel): View = CheckBox(this).apply {
        text = "${channel.channel}   ${channel.name}"
        textSize = 15f
        isChecked = editingChannels.contains(channel.channel)
        setPadding(dpToPx(8), dpToPx(10), dpToPx(8), dpToPx(10))
        setTextColor(ContextCompat.getColor(context, R.color.on_surface))

        setOnCheckedChangeListener { _, checked ->
            if (checked) {
                editingChannels.add(channel.channel)
            } else {
                editingChannels.remove(channel.channel)
            }
            updateEditCount()
        }
    }

    private fun updateEditCount() {
        binding.editCount.text = "${editingChannels.size} of ${channels.size} channels"
    }

    private fun saveEdit() {
        val original = editingName ?: return
        val name = binding.editName.text?.toString()?.trim().orEmpty()

        if (name.isEmpty()) {
            onError("A bank needs a name.")
            return
        }

        if (banks.any { it.name != original && it.name.equals(name, ignoreCase = true) }) {
            onError("There is already a bank called \"$name\".")
            return
        }

        // Replaced in place rather than removed and appended, so a
        // rename does not move the bank to the bottom of the picker.
        val updated = banks.map { bank ->
            if (bank.name == original) {
                CustomBank(name, editingChannels.sorted())
            } else {
                bank
            }
        }

        selectedName = name
        MixerClient.saveCustomBanks(updated)
        showBrowse()
    }

    private fun showBrowse() {
        editingName = null
        binding.editView.isVisible = false
        binding.browseView.isVisible = true
        render()
    }

    private fun dpToPx(dp: Int): Int = (dp * resources.displayMetrics.density).toInt()
}
