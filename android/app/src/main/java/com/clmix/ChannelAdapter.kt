package com.clmix

import android.content.res.ColorStateList
import android.graphics.Color
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.SeekBar
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.clmix.databinding.ItemChannelBinding
import kotlin.math.roundToInt

class ChannelAdapter(
    private val onLevelChanged: (Int, Double) -> Unit,
    private val onMuteToggled: (Int, Boolean) -> Unit,
    private val onDragStart: (Int) -> Unit,
    private val onDragEnd: (Int) -> Unit,
    private val onPanButtonClicked: (ChannelState) -> Unit
) : RecyclerView.Adapter<ChannelAdapter.ViewHolder>() {

    // "Fine" mode (toggled from MixerActivity's top bar): while on, the
    // fader ignores the SeekBar's normal "thumb jumps to touch position"
    // behavior and instead moves at FINE_SENSITIVITY of the finger's own
    // travel distance, for finer control than the widget's ~44dp of
    // rotated travel normally allows.
    var smoothEnabled: Boolean = false

    private var channels: List<ChannelState> = emptyList()

    // What's actually on screen right now, per channel - used to skip
    // rebinding items whose displayed state wouldn't change, which is
    // what was causing every slider to rebind (and visibly pulse) on
    // every ~150ms server push regardless of whether anything moved.
    private val displayedProgress = mutableMapOf<Int, Int>()
    private val displayedPan = mutableMapOf<Int, Double?>()
    private val displayedMuted = mutableMapOf<Int, Boolean>()

    fun updateChannels(
        newChannels: List<ChannelState>,
        draggingChannels: Set<Int>,
        dragReleasedAt: Map<Int, Long> = emptyMap()
    ) {
        val sameChannelSet = channels.size == newChannels.size &&
            channels.map { it.channel } == newChannels.map { it.channel }

        channels = newChannels

        if (!sameChannelSet) {
            notifyDataSetChanged()
            return
        }

        val now = System.currentTimeMillis()

        for (index in newChannels.indices) {
            val channel = newChannels[index]

            // Mirrors the desktop app's DRAG_GRACE_SECONDS: for a short
            // window after release, keep ignoring server-reported levels
            // for this channel too, so a stale/in-flight echo of the
            // pre-release position can't snap the slider back and make
            // it feel jumpy.
            val recentlyReleased =
                dragReleasedAt[channel.channel]?.let { now - it < DRAG_GRACE_MS } ?: false

            if (channel.channel in draggingChannels || recentlyReleased) {
                continue
            }

            val targetProgress = channel.level?.let { levelToProgress(it) }
            val progressChanged = targetProgress != null &&
                displayedProgress[channel.channel] != targetProgress

            val panChanged = displayedPan[channel.channel] != channel.pan

            val muteChanged = displayedMuted[channel.channel] != channel.muted

            if (progressChanged || panChanged || muteChanged) {
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
                applyLevel(channel.channel, progress)
            }

            override fun onStartTrackingTouch(bar: SeekBar?) {
                onDragStart(channel.channel)
            }

            override fun onStopTrackingTouch(bar: SeekBar?) {
                onDragEnd(channel.channel)
            }
        })

        // In fine mode this listener intercepts the drag itself, since a
        // stock SeekBar always snaps its thumb straight to the touch
        // position - there's no way to make dragging feel slower by only
        // changing what value the OnSeekBarChangeListener above reports.
        holder.binding.levelSeekBar.setOnTouchListener(object : View.OnTouchListener {
            private var lastRawX = 0f

            override fun onTouch(view: View, event: MotionEvent): Boolean {
                if (!smoothEnabled) return false

                val bar = view as SeekBar
                val travel = bar.width - bar.paddingStart - bar.paddingEnd

                when (event.action) {
                    MotionEvent.ACTION_DOWN -> {
                        lastRawX = event.x
                        onDragStart(channel.channel)

                        // The stock SeekBar does this itself in its own
                        // onTouchEvent, which we're bypassing here - without
                        // it, channelRecycler (a horizontal RecyclerView)
                        // steals the gesture as soon as it sees any sideways
                        // finger drift, cancelling the drag.
                        view.parent?.requestDisallowInterceptTouchEvent(true)
                    }

                    MotionEvent.ACTION_MOVE -> {
                        val deltaX = event.x - lastRawX
                        lastRawX = event.x

                        if (travel > 0) {
                            val deltaProgress = (deltaX / travel) * bar.max * FINE_SENSITIVITY
                            val newProgress = (bar.progress + deltaProgress.roundToInt())
                                .coerceIn(0, bar.max)

                            if (newProgress != bar.progress) {
                                bar.progress = newProgress
                                applyLevel(channel.channel, newProgress)
                            }
                        }
                    }

                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                        view.parent?.requestDisallowInterceptTouchEvent(false)
                        onDragEnd(channel.channel)
                    }
                }

                return true
            }
        })

        holder.binding.panButton.setOnClickListener {
            val adapterPosition = holder.bindingAdapterPosition
            if (adapterPosition != RecyclerView.NO_POSITION) {
                onPanButtonClicked(channels[adapterPosition])
            }
        }

        holder.binding.muteButton.setOnClickListener {
            val adapterPosition = holder.bindingAdapterPosition
            if (adapterPosition != RecyclerView.NO_POSITION) {
                val current = channels[adapterPosition]
                onMuteToggled(current.channel, !current.muted)
            }
        }

        bindChannelState(holder, channel, position)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int, payloads: MutableList<Any>) {
        if (payloads.contains(PAYLOAD_UPDATE)) {
            bindChannelState(holder, channels[position], position)
            return
        }

        super.onBindViewHolder(holder, position, payloads)
    }

    private fun bindChannelState(holder: ViewHolder, channel: ChannelState, position: Int) {
        // Alternate a subtle background so adjacent channel strips read
        // as visually separate columns instead of blurring together.
        holder.binding.root.setBackgroundColor(
            if (position % 2 == 1) {
                ContextCompat.getColor(holder.binding.root.context, R.color.surface_variant)
            } else {
                Color.TRANSPARENT
            }
        )

        val level = channel.level
        if (level != null) {
            val progress = levelToProgress(level)
            holder.binding.levelSeekBar.progress = progress
            displayedProgress[channel.channel] = progress
        }

        holder.binding.panButton.text = PanFormat.buttonLabel(channel.pan)
        displayedPan[channel.channel] = channel.pan

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

    private fun applyLevel(channel: Int, progress: Int) {
        val fraction = progress.toDouble() / SEEK_MAX
        val db = Math.round(AuxTaper.fractionToDb(fraction) * 100.0) / 100.0
        onLevelChanged(channel, db)
    }

    private fun levelToProgress(db: Double) = (AuxTaper.dbToFraction(db) * SEEK_MAX).toInt()

    override fun getItemCount() = channels.size

    companion object {
        private const val SEEK_MAX = 1000
        private const val PAYLOAD_UPDATE = "update"

        // Matches the desktop app's AuxLevelsPanel.DRAG_GRACE_SECONDS.
        private const val DRAG_GRACE_MS = 300L

        // Fine mode: the thumb moves at 20% of the finger's own travel
        // distance, i.e. 5x slower than a direct 1:1 drag.
        private const val FINE_SENSITIVITY = 0.2
    }
}
