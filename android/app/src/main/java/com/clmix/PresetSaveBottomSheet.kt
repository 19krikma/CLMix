package com.clmix

import android.content.Context
import android.widget.Toast
import com.clmix.databinding.BottomSheetPresetSaveBinding
import com.google.android.material.bottomsheet.BottomSheetDialog

/**
 * Prompts for a name and captures every channel's current level+pan for
 * whatever aux is active, via MixerClient.savePreset - the server reads
 * the actual values from its own cache, so this only needs to send the
 * name. Mirrors PanBottomSheet's structure/style.
 */
class PresetSaveBottomSheet(
    private val context: Context,
    private val onSave: (String) -> Unit
) {
    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetPresetSaveBinding.inflate(dialog.layoutInflater)

    init {
        dialog.setContentView(binding.root)

        binding.presetSaveConfirmButton.setOnClickListener {
            val name = binding.presetNameInput.text?.toString()?.trim().orEmpty()

            if (name.isEmpty()) {
                Toast.makeText(context, "Enter a preset name", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            onSave(name)
        }
    }

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener { action() }
    }
}
