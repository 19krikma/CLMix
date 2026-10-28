package com.clmix

import android.content.Context
import android.view.View
import com.clmix.databinding.BottomSheetPresetManageBinding
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.checkbox.MaterialCheckBox
import com.google.android.material.dialog.MaterialAlertDialogBuilder

/**
 * One of this account's own presets: whether it is public, which other
 * accounts it is shared with, and deleting it. Every change goes to the
 * server straight away - there is no Save - and the server answers with a
 * fresh presets list, fed back in through [update], so what this shows is
 * always what the server has rather than what was tapped.
 */
class PresetManageBottomSheet(
    private val context: Context,
    private var preset: Preset
) {
    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetPresetManageBinding.inflate(dialog.layoutInflater)

    /** Which preset this is, so the activity can route updates to it. */
    val presetId: String get() = preset.id

    private var targets: List<String>? = null
    private val boxes = mutableMapOf<String, MaterialCheckBox>()

    init {
        dialog.setContentView(binding.root)

        binding.presetPublicSwitch.setOnCheckedChangeListener { _, checked ->
            if (checked != preset.public) MixerClient.setPresetPublic(preset.id, checked)
        }

        binding.presetDeleteButton.setOnClickListener {
            MaterialAlertDialogBuilder(context)
                .setTitle("Delete preset")
                .setMessage("Delete \"${preset.name}\"? Anyone it is shared with loses it too.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Delete") { _, _ ->
                    MixerClient.deletePreset(preset.id)
                    dialog.dismiss()
                }
                .show()
        }

        render()
        MixerClient.requestShareTargets(preset.id)
    }

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener { action() }
    }

    /** The server's fresh copy of this preset - or null once it is gone. */
    fun update(fresh: Preset?) {
        if (fresh == null) {
            dialog.dismiss()
            return
        }

        preset = fresh
        render()
    }

    fun setShareTargets(users: List<String>, names: Map<String, String> = emptyMap()) {
        targets = users
        boxes.clear()
        binding.presetShareList.removeAllViews()

        for (user in users) {
            val box = MaterialCheckBox(context).apply {
                text = names[user] ?: user
                minHeight = (48 * resources.displayMetrics.density).toInt()
                isSaveEnabled = false
            }
            box.setOnCheckedChangeListener { _, _ -> sendShares() }
            boxes[user] = box
            binding.presetShareList.addView(box)
        }

        render()
    }

    private fun sendShares() {
        val users = boxes.filterValues { it.isChecked }.keys.sorted()
        if (users != preset.sharedWith.sorted()) MixerClient.sharePreset(preset.id, users)
    }

    private fun render() {
        binding.presetManageTitle.text = preset.name
        binding.presetPublicSwitch.isChecked = preset.public

        // Checked state set without a listener firing a share of its own.
        for ((user, box) in boxes) {
            val shared = user in preset.sharedWith
            if (box.isChecked != shared) {
                box.setOnCheckedChangeListener(null)
                box.isChecked = shared
                box.setOnCheckedChangeListener { _, _ -> sendShares() }
            }
        }

        val loaded = targets
        binding.presetShareEmptyLabel.visibility =
            if (loaded == null || loaded.isEmpty()) View.VISIBLE else View.GONE
        binding.presetShareEmptyLabel.text = if (loaded == null) {
            "Loading accounts…"
        } else {
            "No other accounts have preset access."
        }
    }
}
