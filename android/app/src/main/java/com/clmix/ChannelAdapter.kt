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
    private val onDragStart: (Int) -> Unit,
    private val onDragEnd: (Int) -> Unit,
    private val onPanButtonClicked: (ChannelState) -> Unit,
    private val onMuteToggled: (Int, Boolean) -> Unit
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

    // A mute tap flips the button straight away rather than waiting for
    // the console's echo to travel back through the desktop app's cache
    // and the next push (~150ms at best, longer over a busy wifi) - but
    // the server stays the authority on what's actually muted. Until a
    // push agrees with what was asked for, pushes for that channel's
    // mute are ignored, so an in-flight one describing the pre-tap state
    // can't flip the button back and forth. After MUTE_CONFIRM_TIMEOUT_MS
    // without agreement the request is assumed lost - the console's own
    // state wins again and the button snaps back to the truth.
    private class PendingMute(val expected: Boolean, val sentAt: Long)

    private val pendingMutes = mutableMapOf<Int, PendingMute>()

    fun updateChannels(
        newChannels: List<ChannelState>,
        draggingChannels: Set<Int>,
        dragReleasedAt: Map<Int, Long> = emptyMap()
    ) {
        val sameChannelSet = channels.size == newChannels.size &&
            channels.map { it.channel } == newChannels.map { it.channel }

        channels = newChannels

        if (!sameChannelSet) {
            // A bank switch replaces the visible strips - a tap still
            // awaiting confirmation on a channel that just left the list
            // would otherwise sit here suppressing that channel's real
            // state for the next couple of seconds if it came back.
            pendingMutes.keys.retainAll(newChannels.map { it.channel }.toSet())
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

            val muteChanged = displayedMuted[channel.channel] != effectiveMuted(channel)

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

        // The level fader is a SeekBar rotated 270° - its layout width
        // becomes its visual length once rotated, but XML has no way to
        // express "width = parent's height" for a rotated view. Instead,
        // whenever fader_row's actual height changes (initial layout,
        // orientation change), resize the SeekBar to match it exactly -
        // otherwise the fader is stuck at a guessed fixed length, wasting
        // space when more is available and clipping the ruler drawn
        // beside it (LevelRulerView, which does size itself off the
        // row's real height) when less is.
        binding.faderRow.addOnLayoutChangeListener { _, _, top, _, bottom, _, _, _, _ ->
            syncFaderWidth(binding, bottom - top)
        }

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

        // Toggles against what's on screen rather than against the last
        // pushed state, so tapping twice in quick succession (before the
        // first change has come back) reliably ends up where the user
        // left it instead of re-sending the same value.
        holder.binding.muteButton.setOnClickListener {
            val adapterPosition = holder.bindingAdapterPosition
            if (adapterPosition == RecyclerView.NO_POSITION) return@setOnClickListener

            val tapped = channels[adapterPosition]
            val target = !(displayedMuted[tapped.channel] ?: tapped.muted)

            pendingMutes[tapped.channel] = PendingMute(target, System.currentTimeMillis())
            displayedMuted[tapped.channel] = target
            applyMuteAppearance(holder.binding, target)

            onMuteToggled(tapped.channel, target)
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
        // A rebind (e.g. after notifyDataSetChanged() on a bank switch)
        // can reuse a ViewHolder whose fader_row is already measured but
        // whose SeekBar width was set for a size that no longer applies -
        // the layout listener above only fires on an actual height
        // *change*, which a rebind at the same on-screen size never
        // triggers. Re-check on every bind so the fader can't get stuck
        // at a stale length.
        syncFaderWidth(holder.binding, holder.binding.faderRow.height)

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

        val muted = effectiveMuted(channel)
        displayedMuted[channel.channel] = muted
        applyMuteAppearance(holder.binding, muted)
    }

    // What this channel's Mute button should read right now: the pushed
    // state, unless a tap on it is still waiting to be confirmed (see
    // pendingMutes). Resolving a pending one here - rather than on a
    // timer - means it's settled at exactly the moments its answer is
    // needed, on every push and every rebind.
    private fun effectiveMuted(channel: ChannelState): Boolean {
        val pending = pendingMutes[channel.channel] ?: return channel.muted

        val confirmed = channel.muted == pending.expected
        val timedOut = System.currentTimeMillis() - pending.sentAt >= MUTE_CONFIRM_TIMEOUT_MS

        if (confirmed || timedOut) {
            pendingMutes.remove(channel.channel)
            return channel.muted
        }

        return pending.expected
    }

    private fun applyMuteAppearance(binding: ItemChannelBinding, muted: Boolean) {
        val context = binding.root.context

        binding.muteButton.text = if (muted) "Muted" else "Mute"
        binding.muteButton.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(
                context, if (muted) R.color.mute_active else R.color.mute_inactive
            )
        )
        binding.muteButton.setTextColor(
            ContextCompat.getColor(
                context, if (muted) R.color.on_primary else R.color.on_mute_inactive
            )
        )
    }

    // Called from MixerActivity whenever channel_recycler's own height
    // changes (notably: the status/nav bars hiding shortly after first
    // layout, since that resize happens after the recycler's initial
    // layout pass). A channel whose fader was measured against the
    // pre-resize height and never rebinds afterwards (its level, pan and
    // mute never change) would otherwise be stuck at that wrong length
    // forever, since nothing else ever re-triggers its own sync.
    fun syncAllFaderWidths(recyclerView: RecyclerView) {
        for (i in 0 until recyclerView.childCount) {
            val holder = recyclerView.getChildViewHolder(recyclerView.getChildAt(i)) as? ViewHolder
                ?: continue
            syncFaderWidth(holder.binding, holder.binding.faderRow.height)
        }
    }

    private fun syncFaderWidth(binding: ItemChannelBinding, height: Int) {
        if (height <= 0) return

        val bar = binding.levelSeekBar
        if (bar.layoutParams.width == height) return

        // Deferred via post(): this is sometimes called from fader_row's
        // own OnLayoutChangeListener, which fires *during* RecyclerView's
        // own active layout pass. A requestLayout() triggered synchronously
        // from in there (which assigning layoutParams below does) gets
        // silently swallowed by RecyclerView's layout-request suppression -
        // the width value changes but the SeekBar never actually
        // re-measures. Posting runs this after the current pass finishes,
        // outside that suppression window, so the resize actually takes.
        bar.post {
            val params = bar.layoutParams
            if (params.width != height) {
                params.width = height
                bar.layoutParams = params
            }
        }
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

        // How long a tapped Mute button holds its own state before
        // deferring to the console again - long enough to cover the
        // round trip through the desktop app on a slow network, short
        // enough that a command the console never acted on doesn't leave
        // a strip lying about being muted.
        private const val MUTE_CONFIRM_TIMEOUT_MS = 2000L
    }
}
