package com.digico.monitormix

import android.content.res.ColorStateList
import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.SeekBar
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.digico.monitormix.databinding.ItemChannelBinding

class ChannelAdapter(
    private val onLevelChanged: (Int, Double) -> Unit,
    private val onPanChanged: (Int, Double) -> Unit,
    private val onMuteToggled: (Int, Boolean) -> Unit,
    private val onDragStart: (Int) -> Unit,
    private val onDragEnd: (Int) -> Unit,
    private val onPanDragStart: (Int) -> Unit,
    private val onPanDragEnd: (Int) -> Unit
) : RecyclerView.Adapter<ChannelAdapter.ViewHolder>() {

    private var channels: List<ChannelState> = emptyList()

    // What's actually on screen right now, per channel - used to skip
    // rebinding items whose displayed state wouldn't change, which is
    // what was causing every slider to rebind (and visibly pulse) on
    // every ~150ms server push regardless of whether anything moved.
    private val displayedProgress = mutableMapOf<Int, Int>()
    private val displayedPanProgress = mutableMapOf<Int, Int>()
    private val displayedMuted = mutableMapOf<Int, Boolean>()

    fun updateChannels(
        newChannels: List<ChannelState>,
        draggingChannels: Set<Int>,
        panDraggingChannels: Set<Int>
    ) {
        val sameChannelSet = channels.size == newChannels.size &&
            channels.map { it.channel } == newChannels.map { it.channel }

        channels = newChannels

        if (!sameChannelSet) {
            notifyDataSetChanged()
            return
        }

        for (index in newChannels.indices) {
            val channel = newChannels[index]
            if (channel.channel in draggingChannels || channel.channel in panDraggingChannels) {
                continue
            }

            val targetProgress = channel.level?.let { levelToProgress(it) }
            val progressChanged = targetProgress != null &&
                displayedProgress[channel.channel] != targetProgress

            val targetPanProgress = channel.pan?.let { panToProgress(it) }
            val panProgressChanged = targetPanProgress != null &&
                displayedPanProgress[channel.channel] != targetPanProgress

            val muteChanged = displayedMuted[channel.channel] != channel.muted

            if (progressChanged || panProgressChanged || muteChanged) {
                notifyItemChanged(index, PAYLOAD_UPDATE)
            }
        }
    }

    inner class ViewHolder(val binding: ItemChannelBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemChannelBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val channel = channels[position]

        holder.binding.channelName.text = channel.name

        holder.binding.levelSeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser) return

                val fraction = progress.toDouble() / SEEK_MAX
                val db = Math.round(AuxTaper.fractionToDb(fraction) * 100.0) / 100.0
                onLevelChanged(channel.channel, db)
            }

            override fun onStartTrackingTouch(bar: SeekBar?) = onDragStart(channel.channel)
            override fun onStopTrackingTouch(bar: SeekBar?) = onDragEnd(channel.channel)
        })

        holder.binding.panSeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser) return

                onPanChanged(channel.channel, progressToPan(progress))
            }

            override fun onStartTrackingTouch(bar: SeekBar?) = onPanDragStart(channel.channel)
            override fun onStopTrackingTouch(bar: SeekBar?) = onPanDragEnd(channel.channel)
        })

        holder.binding.muteButton.setOnClickListener {
            val adapterPosition = holder.bindingAdapterPosition
            if (adapterPosition != RecyclerView.NO_POSITION) {
                val current = channels[adapterPosition]
                onMuteToggled(current.channel, !current.muted)
            }
        }

        bindChannelState(holder, channel)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int, payloads: MutableList<Any>) {
        if (payloads.contains(PAYLOAD_UPDATE)) {
            bindChannelState(holder, channels[position])
            return
        }

        super.onBindViewHolder(holder, position, payloads)
    }

    private fun bindChannelState(holder: ViewHolder, channel: ChannelState) {
        val level = channel.level
        if (level != null) {
            val progress = levelToProgress(level)
            holder.binding.levelSeekBar.progress = progress
            displayedProgress[channel.channel] = progress
        }

        val pan = channel.pan
        if (pan != null) {
            val panProgress = panToProgress(pan)
            holder.binding.panSeekBar.progress = panProgress
            displayedPanProgress[channel.channel] = panProgress
        }

        displayedMuted[channel.channel] = channel.muted

        holder.binding.muteButton.text = if (channel.muted) "Muted" else "Mute"
        holder.binding.muteButton.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(
                holder.binding.root.context,
                if (channel.muted) R.color.mute_active else R.color.mute_inactive
            )
        )
        holder.binding.muteButton.setTextColor(
            ContextCompat.getColor(
                holder.binding.root.context,
                if (channel.muted) R.color.on_primary else R.color.on_mute_inactive
            )
        )
    }

    private fun levelToProgress(db: Double) = (AuxTaper.dbToFraction(db) * SEEK_MAX).toInt()

    // Pan is a plain linear value (-1.0 hard left .. +1.0 hard right,
    // 0.0 center) - unlike level it has no dB-style taper to apply.
    private fun panToProgress(pan: Double) = (((pan + 1.0) / 2.0) * PAN_SEEK_MAX).toInt()

    private fun progressToPan(progress: Int): Double {
        val raw = (progress.toDouble() / PAN_SEEK_MAX) * 2.0 - 1.0
        return Math.round(raw * 100.0) / 100.0
    }

    override fun getItemCount() = channels.size

    companion object {
        private const val SEEK_MAX = 1000
        private const val PAN_SEEK_MAX = 200
        private const val PAYLOAD_UPDATE = "update"
    }
}
