package com.clmix

import android.content.Context
import android.widget.FrameLayout
import android.widget.SeekBar
import androidx.core.content.ContextCompat
import com.clmix.databinding.BottomSheetPanBinding
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.R as MaterialR

/**
 * Full-width pan control for a single channel, shown from that channel's
 * "Pan" button. Replaces the old always-visible per-channel horizontal
 * pan SeekBar, which at ~90dp wide (the channel column's width) gave too
 * little drag travel for precise pan moves - this reuses the full screen
 * width instead.
 */
class PanBottomSheet(
    context: Context,
    val channel: Int,
    channelName: String,
    initialPan: Double,
    private val onPanChanged: (Int, Double) -> Unit
) {
    private val dialog = BottomSheetDialog(context)
    private val binding = BottomSheetPanBinding.inflate(dialog.layoutInflater)

    private var dragging = false
    private var dragReleasedAt = 0L

    init {
        dialog.setContentView(binding.root)

        // In landscape the sheet's default "collapsed" peek height is
        // sized for portrait content and cuts off everything below the
        // value label - the slider included. Force it fully expanded so
        // the whole sheet is always visible regardless of orientation.
        dialog.setOnShowListener {
            val bottomSheet = dialog.findViewById<FrameLayout>(MaterialR.id.design_bottom_sheet)
            bottomSheet?.let {
                val behavior = BottomSheetBehavior.from(it)
                behavior.state = BottomSheetBehavior.STATE_EXPANDED
                behavior.skipCollapsed = true
            }
        }

        val density = context.resources.displayMetrics.density
        binding.panSeekBarLarge.progressDrawable = PanTrackDrawable(
            trackColor = ContextCompat.getColor(context, R.color.track_background),
            fillColor = ContextCompat.getColor(context, R.color.primary),
            trackHeightPx = 10 * density,
            cornerRadiusPx = 5 * density
        )

        binding.panChannelName.text = channelName
        binding.panSeekBarLarge.progress = PanFormat.toProgress(initialPan)
        binding.panValueLabel.text = PanFormat.shortLabel(initialPan)

        binding.panSeekBarLarge.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, progress: Int, fromUser: Boolean) {
                if (!fromUser) return
                applyProgress(progress, notify = true)
            }

            override fun onStartTrackingTouch(bar: SeekBar?) {
                dragging = true
            }

            override fun onStopTrackingTouch(bar: SeekBar?) {
                dragging = false
                dragReleasedAt = System.currentTimeMillis()
            }
        })

        binding.panCenterButton.setOnClickListener {
            val center = PanFormat.SEEK_MAX / 2
            binding.panSeekBarLarge.progress = center
            applyProgress(center, notify = true)
        }
    }

    fun show() = dialog.show()

    fun dismiss() = dialog.dismiss()

    fun setOnDismissListener(action: () -> Unit) {
        dialog.setOnDismissListener { action() }
    }

    // Called whenever a fresh pan value arrives from the server for this
    // channel, so the sheet stays live while it's open. Ignored while the
    // user is actively dragging (or just released) so a stale/in-flight
    // echo of an earlier position can't snap the thumb back - mirrors the
    // desktop app's DRAG_GRACE_SECONDS.
    fun updateIfIdle(pan: Double) {
        if (dragging || System.currentTimeMillis() - dragReleasedAt < DRAG_GRACE_MS) return

        val progress = PanFormat.toProgress(pan)
        if (binding.panSeekBarLarge.progress != progress) {
            binding.panSeekBarLarge.progress = progress
            binding.panValueLabel.text = PanFormat.shortLabel(pan)
        }
    }

    private fun applyProgress(progress: Int, notify: Boolean) {
        val pan = PanFormat.toPan(progress)
        binding.panValueLabel.text = PanFormat.shortLabel(pan)

        if (notify) {
            onPanChanged(channel, pan)
        }
    }

    companion object {
        private const val DRAG_GRACE_MS = 300L
    }
}
