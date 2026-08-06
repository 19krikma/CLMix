package com.digico.monitormix

import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.GestureDetector
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.ViewGroup
import android.widget.SeekBar
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import com.digico.monitormix.databinding.ItemChannelBinding
import kotlin.math.abs
import kotlin.math.roundToInt
import kotlin.math.sign

class ChannelAdapter(
    private val onLevelChanged: (Int, Double) -> Unit,
    private val onPanChanged: (Int, Double) -> Unit,
    private val onMuteToggled: (Int, Boolean) -> Unit,
    private val onDragStart: (Int) -> Unit,
    private val onDragEnd: (Int) -> Unit,
    private val onPanDragStart: (Int) -> Unit,
    private val onPanDragEnd: (Int) -> Unit
) : RecyclerView.Adapter<ChannelAdapter.ViewHolder>() {

    // "Smooth" mode (toggled from MixerActivity's top bar): while on, a
    // dragged fader/pan slider doesn't jump to the touch position - it
    // eases toward it at GLIDE_PERCENT_PER_SECOND and freezes wherever
    // it's reached the instant the user releases.
    var smoothEnabled: Boolean = false

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
        panDraggingChannels: Set<Int>,
        dragReleasedAt: Map<Int, Long> = emptyMap(),
        panDragReleasedAt: Map<Int, Long> = emptyMap()
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
                (dragReleasedAt[channel.channel]?.let { now - it < DRAG_GRACE_MS } ?: false) ||
                (panDragReleasedAt[channel.channel]?.let { now - it < DRAG_GRACE_MS } ?: false)

            if (channel.channel in draggingChannels ||
                channel.channel in panDraggingChannels ||
                recentlyReleased
            ) {
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

    inner class ViewHolder(val binding: ItemChannelBinding) : RecyclerView.ViewHolder(binding.root) {
        private val panGestureDetector = GestureDetector(
            binding.root.context,
            object : GestureDetector.SimpleOnGestureListener() {
                override fun onDoubleTap(e: MotionEvent): Boolean {
                    val position = bindingAdapterPosition
                    if (position == RecyclerView.NO_POSITION) return false

                    val channel = channels[position].channel
                    val centerProgress = PAN_SEEK_MAX / 2

                    binding.panSeekBar.progress = centerProgress
                    displayedPanProgress[channel] = centerProgress
                    onPanChanged(channel, 0.0)
                    return true
                }
            }
        )

        init {
            binding.panSeekBar.setOnTouchListener { view, event ->
                panGestureDetector.onTouchEvent(event)
                view.onTouchEvent(event)
            }
        }

        // --- Smooth-mode glide (level) ---

        private val glideHandler = Handler(Looper.getMainLooper())
        private var levelGlideChannel = -1
        private var levelGlideCurrent = 0.0
        private var levelGlideTarget = 0.0
        private val levelGlideRunnable = object : Runnable {
            override fun run() {
                levelGlideCurrent = approach(levelGlideCurrent, levelGlideTarget, LEVEL_GLIDE_STEP)
                val progress = levelGlideCurrent.roundToInt()
                binding.levelSeekBar.progress = progress
                displayedProgress[levelGlideChannel] = progress

                val db = Math.round(
                    AuxTaper.fractionToDb(levelGlideCurrent / SEEK_MAX) * 100.0
                ) / 100.0
                onLevelChanged(levelGlideChannel, db)

                glideHandler.postDelayed(this, GLIDE_INTERVAL_MS)
            }
        }

        fun startLevelGlide(channel: Int, startProgress: Int) {
            stopLevelGlide()
            levelGlideChannel = channel
            levelGlideCurrent = startProgress.toDouble()
            levelGlideTarget = startProgress.toDouble()
            glideHandler.post(levelGlideRunnable)
        }

        fun updateLevelGlideTarget(progress: Int) {
            levelGlideTarget = progress.toDouble()
        }

        fun stopLevelGlide() {
            glideHandler.removeCallbacks(levelGlideRunnable)
        }

        // --- Smooth-mode glide (pan) ---

        private var panGlideChannel = -1
        private var panGlideCurrent = 0.0
        private var panGlideTarget = 0.0
        private val panGlideRunnable = object : Runnable {
            override fun run() {
                panGlideCurrent = approach(panGlideCurrent, panGlideTarget, PAN_GLIDE_STEP)
                val progress = panGlideCurrent.roundToInt()
                binding.panSeekBar.progress = progress
                displayedPanProgress[panGlideChannel] = progress

                onPanChanged(panGlideChannel, progressToPan(progress))

                glideHandler.postDelayed(this, GLIDE_INTERVAL_MS)
            }
        }

        fun startPanGlide(channel: Int, startProgress: Int) {
            stopPanGlide()
            panGlideChannel = channel
            panGlideCurrent = startProgress.toDouble()
            panGlideTarget = startProgress.toDouble()
            glideHandler.post(panGlideRunnable)
        }

        fun updatePanGlideTarget(progress: Int) {
            panGlideTarget = progress.toDouble()
        }

        fun stopPanGlide() {
            glideHandler.removeCallbacks(panGlideRunnable)
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemChannelBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val channel = channels[position]

        // This holder may be a recycled view still running a glide for
        // whatever channel it previously displayed - clear it before
        // rebinding, so stray ticks can't land on the wrong channel.
        holder.stopLevelGlide()
        holder.stopPanGlide()

        holder.binding.channelName.text = channel.name

        holder.binding.levelSeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser) return

                if (smoothEnabled) {
                    holder.updateLevelGlideTarget(progress)
                } else {
                    val fraction = progress.toDouble() / SEEK_MAX
                    val db = Math.round(AuxTaper.fractionToDb(fraction) * 100.0) / 100.0
                    onLevelChanged(channel.channel, db)
                }
            }

            override fun onStartTrackingTouch(bar: SeekBar?) {
                onDragStart(channel.channel)
                if (smoothEnabled) {
                    // Not bar?.progress: a SeekBar snaps its thumb to the
                    // touch-down point immediately, before this callback
                    // even fires, so that would already be the jumped
                    // position rather than the fader's true prior value.
                    val startProgress = displayedProgress[channel.channel]
                        ?: bar?.progress ?: 0
                    holder.startLevelGlide(channel.channel, startProgress)
                }
            }

            override fun onStopTrackingTouch(bar: SeekBar?) {
                holder.stopLevelGlide()
                onDragEnd(channel.channel)
            }
        })

        holder.binding.panSeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser) return

                if (smoothEnabled) {
                    holder.updatePanGlideTarget(progress)
                } else {
                    onPanChanged(channel.channel, progressToPan(progress))
                }
            }

            override fun onStartTrackingTouch(bar: SeekBar?) {
                onPanDragStart(channel.channel)
                if (smoothEnabled) {
                    val startProgress = displayedPanProgress[channel.channel]
                        ?: bar?.progress ?: 0
                    holder.startPanGlide(channel.channel, startProgress)
                }
            }

            override fun onStopTrackingTouch(bar: SeekBar?) {
                holder.stopPanGlide()
                onPanDragEnd(channel.channel)
            }
        })

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

    // Eases "current" toward "target" by at most maxStep, without overshooting.
    private fun approach(current: Double, target: Double, maxStep: Double): Double {
        val diff = target - current
        return if (abs(diff) <= maxStep) target else current + maxStep * sign(diff)
    }

    companion object {
        private const val SEEK_MAX = 1000
        private const val PAN_SEEK_MAX = 200
        private const val PAYLOAD_UPDATE = "update"

        // Matches the desktop app's AuxLevelsPanel.DRAG_GRACE_SECONDS.
        private const val DRAG_GRACE_MS = 300L

        // Smooth mode: 2% of full travel per second, ticked every 50ms.
        private const val GLIDE_INTERVAL_MS = 50L
        private const val GLIDE_PERCENT_PER_SECOND = 0.02
        private val LEVEL_GLIDE_STEP =
            SEEK_MAX * GLIDE_PERCENT_PER_SECOND * GLIDE_INTERVAL_MS / 1000.0
        private val PAN_GLIDE_STEP =
            PAN_SEEK_MAX * GLIDE_PERCENT_PER_SECOND * GLIDE_INTERVAL_MS / 1000.0
    }
}
