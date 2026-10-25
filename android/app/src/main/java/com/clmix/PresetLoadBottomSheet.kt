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
 * Lists every preset this account can see - its own, ones shared with it,
 * and public ones (populated via MixerClient.requestPresets/onPresets) -
 * and requires picking one, then pressing Load to confirm - unlike the
 * aux list this doesn't apply on tap alone, since loading a preset
 * overwrites every channel's current level+pan on the active aux.
 *
 * Each row says whose preset it is and who can see it; the account's own
 * carry a button into PresetManageBottomSheet.
 */
class PresetLoadBottomSheet(
    context: Context,
    auxName: String,
    private val onLoad: (Preset) -> Unit,
    onManage: (Preset) -> Unit
) {
    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetPresetLoadBinding.inflate(dialog.layoutInflater)
    private val adapter = PresetAdapter({ updateLoadButtonEnabled() }, onManage)

    init {
        dialog.setContentView(binding.root)
        // The server only lists this aux's presets - see
        // RemoteServer._handle_preset_action.
        binding.presetLoadAuxLabel.text = "For $auxName"

        binding.presetListRecycler.layoutManager = LinearLayoutManager(context)
        binding.presetListRecycler.adapter = adapter

        binding.presetLoadConfirmButton.setOnClickListener {
            adapter.selected?.let(onLoad)
        }
    }

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener { action() }
    }

    fun setPresets(presets: List<Preset>) {
        adapter.submit(presets)
        binding.presetLoadEmptyLabel.visibility = if (presets.isEmpty()) View.VISIBLE else View.GONE
        binding.presetListRecycler.visibility = if (presets.isEmpty()) View.GONE else View.VISIBLE
        updateLoadButtonEnabled()
    }

    private fun updateLoadButtonEnabled() {
        binding.presetLoadConfirmButton.isEnabled = adapter.selected != null
    }
}

private class PresetAdapter(
    private val onSelectionChanged: () -> Unit,
    private val onManage: (Preset) -> Unit
) : RecyclerView.Adapter<PresetAdapter.ViewHolder>() {

    private var items: List<Preset> = emptyList()

    // By key rather than by object: a fresh list after a share or a
    // public toggle carries new Preset values for the same presets, and
    // the selection should survive that.
    private var selectedKey: String? = null

    val selected: Preset?
        get() = items.firstOrNull { key(it) == selectedKey }

    class ViewHolder(val binding: ItemPresetBinding) : RecyclerView.ViewHolder(binding.root)

    // An old server gives no ids, and names are unique there.
    private fun key(preset: Preset) = preset.id.ifEmpty { preset.name }

    fun submit(presets: List<Preset>) {
        items = presets

        if (selected == null) {
            selectedKey = null
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
        val preset = items[position]
        val context = holder.binding.root.context
        val density = context.resources.displayMetrics.density
        val isSelected = key(preset) == selectedKey

        holder.binding.presetName.text = preset.name
        holder.binding.presetSubtitle.text = preset.subtitle
        holder.binding.root.strokeColor = ContextCompat.getColor(
            context, if (isSelected) R.color.primary else R.color.surface_variant
        )
        holder.binding.root.strokeWidth = ((if (isSelected) 2 else 1) * density).toInt()

        holder.binding.presetManageButton.visibility = if (preset.mine) View.VISIBLE else View.GONE
        holder.binding.presetManageButton.setOnClickListener { onManage(preset) }

        holder.binding.root.setOnClickListener {
            selectedKey = key(preset)
            notifyDataSetChanged()
            onSelectionChanged()
        }
    }

    override fun getItemCount() = items.size
}
