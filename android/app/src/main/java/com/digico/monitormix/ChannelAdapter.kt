package com.digico.monitormix

import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.SeekBar
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.digico.monitormix.databinding.ItemChannelBinding

class ChannelAdapter(
    private val onLevelChanged: (Int, Double) -> Unit,
    private val onMuteToggled: (Int, Boolean) -> Unit,
    private val onDragStart: (Int) -> Unit,
    private val onDragEnd: (Int) -> Unit
) : RecyclerView.Adapter<ChannelAdapter.ViewHolder>() {

    private var channels: List<ChannelState> = emptyList()

    // What's actually on screen right now, per channel - used to skip
    // rebinding items whose displayed state wouldn't change, which is
    // what was causing every slider to rebind (and visibly pulse) on
    // every ~150ms server push regardless of whether anything moved.
    private val displayedProgress = mutableMapOf<Int, Int>()
    private val displayedMuted = mutableMapOf<Int, Boolean>()

    fun updateChannels(newChannels: List<ChannelState>, draggingChannels: Set<Int>) {
        val sameChannelSet = channels.size == newChannels.size &&
            channels.map { it.channel } == newChannels.map { it.channel }

        channels = newChannels

        if (!sameChannelSet) {
            notifyDataSetChanged()
            return
        }

        for (index in newChannels.indices) {
            val channel = newChannels[index]
            if (channel.channel in draggingChannels) continue

            val targetProgress = channel.level?.let { levelToProgress(it) }
            val progressChanged = targetProgress != null &&
                displayedProgress[channel.channel] != targetProgress
            val muteChanged = displayedMuted[channel.channel] != channel.muted

            if (progressChanged || muteChanged) {
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

        holder.binding.muteButton.setOnClickListener {
            val adapterPosition = holder.bindingAdapterPosition
            if (adapterPosition != RecyclerView.NO_POSITION) {
                val current = channels[adapterPosition]
                onMuteToggled(current.channel, !current.muted)
            }
        }

        bindLevelAndMute(holder, channel)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int, payloads: MutableList<Any>) {
        if (payloads.contains(PAYLOAD_UPDATE)) {
            bindLevelAndMute(holder, channels[position])
            return
        }

        super.onBindViewHolder(holder, position, payloads)
    }

    private fun bindLevelAndMute(holder: ViewHolder, channel: ChannelState) {
        val level = channel.level
        if (level != null) {
            val progress = levelToProgress(level)
            holder.binding.levelSeekBar.progress = progress
            displayedProgress[channel.channel] = progress
        }

        displayedMuted[channel.channel] = channel.muted

        holder.binding.muteButton.text = if (channel.muted) "Muted" else "Mute"
        holder.binding.muteButton.setBackgroundColor(
            if (channel.muted) {
                ContextCompat.getColor(holder.binding.root.context, R.color.mute_active)
            } else {
                ContextCompat.getColor(holder.binding.root.context, android.R.color.darker_gray)
            }
        )
    }

    private fun levelToProgress(db: Double) = (AuxTaper.dbToFraction(db) * SEEK_MAX).toInt()

    override fun getItemCount() = channels.size

    companion object {
        private const val SEEK_MAX = 1000
        private const val PAYLOAD_UPDATE = "update"
    }
}
