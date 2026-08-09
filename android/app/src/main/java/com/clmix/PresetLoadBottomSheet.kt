package com.clmix

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.clmix.databinding.BottomSheetPresetLoadBinding
import com.clmix.databinding.ItemPresetBinding
import com.google.android.material.bottomsheet.BottomSheetDialog

/**
 * Lists every saved preset (populated via MixerClient.requestPresets/
 * onPresets) and requires picking one, then pressing Load to confirm -
 * unlike the aux list this doesn't apply on tap alone, since loading a
 * preset overwrites every channel's current level+pan on the active aux.
 */
class PresetLoadBottomSheet(
    context: Context,
    private val onLoad: (String) -> Unit
) {
    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetPresetLoadBinding.inflate(dialog.layoutInflater)
    private val adapter = PresetAdapter { updateLoadButtonEnabled() }

    init {
        dialog.setContentView(binding.root)

        binding.presetListRecycler.layoutManager = LinearLayoutManager(context)
        binding.presetListRecycler.adapter = adapter

        binding.presetLoadConfirmButton.setOnClickListener {
            adapter.selectedName?.let(onLoad)
        }
    }

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener { action() }
    }

    fun setPresets(names: List<String>) {
        adapter.submit(names)
        binding.presetLoadEmptyLabel.visibility = if (names.isEmpty()) View.VISIBLE else View.GONE
        binding.presetListRecycler.visibility = if (names.isEmpty()) View.GONE else View.VISIBLE
        updateLoadButtonEnabled()
    }

    private fun updateLoadButtonEnabled() {
        binding.presetLoadConfirmButton.isEnabled = adapter.selectedName != null
    }
}

private class PresetAdapter(
    private val onSelectionChanged: () -> Unit
) : RecyclerView.Adapter<PresetAdapter.ViewHolder>() {

    private var items: List<String> = emptyList()

    var selectedName: String? = null
        private set

    class ViewHolder(val binding: ItemPresetBinding) : RecyclerView.ViewHolder(binding.root)

    fun submit(names: List<String>) {
        items = names

        if (selectedName != null && selectedName !in names) {
            selectedName = null
        }

        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemPresetBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val name = items[position]
        val context = holder.binding.root.context
        val density = context.resources.displayMetrics.density
        val isSelected = name == selectedName

        holder.binding.presetName.text = name
        holder.binding.root.strokeColor = ContextCompat.getColor(
            context, if (isSelected) R.color.primary else R.color.surface_variant
        )
        holder.binding.root.strokeWidth = ((if (isSelected) 2 else 1) * density).toInt()

        holder.binding.root.setOnClickListener {
            selectedName = name
            notifyDataSetChanged()
            onSelectionChanged()
        }
    }

    override fun getItemCount() = items.size
}
