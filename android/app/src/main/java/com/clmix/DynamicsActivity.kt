package com.clmix

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.widget.Space
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.clmix.databinding.ActivityDynamicsBinding
import com.google.android.material.button.MaterialButton
import kotlin.math.log2
import kotlin.math.pow

private const val EXTRA_CHANNEL = "channel"
private const val EXTRA_NAME = "name"
private const val EXTRA_COMP = "comp"
private const val STATE_COMP = "comp"

/**
 * One input channel's compressor and gate, full screen and on its side.
 *
 * The EQ screen's twin (EqActivity), opened from a strip's COMP or GATE
 * button with that section selected. Opened from COMP it is Dynamics 1
 * alone - compressor, multiband or de-esser, never a gate (captured on
 * channel 43, 2026-10-24) - so there is no COMP/GATE switch and the graph
 * draws the compressor only. Opened from GATE it offers both. The socket stays in mixer mode and
 * select_dyn has the server follow each levels push with a "dynamics"
 * frame for this channel.
 *
 * Each section has five dials - threshold, ratio or range, attack,
 * release or hold, makeup gain - and an IN button; the graph shows the
 * static curve both sections make together, with each threshold a handle.
 * Edits move the screen at once and reach the desk at a capped rate, and
 * the desk's echo of a value being moved is ignored for a moment after,
 * exactly as on the EQ screen.
 *
 * Only the compressor's ordinary single-band settings and the gate's are
 * offered. When the console reports the compressor running multiband or
 * de-esser, or the gate slot ducking or compressing, the screen says so
 * and shows the first band's settings - it never changes the mode itself.
 */
class DynamicsActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityDynamicsBinding

    private var channel = 0
    private var dynamics = ChannelDynamics(0)
    private var compSelected = true
    private var compOnly = false

    private val handler = Handler(Looper.getMainLooper())
    private val heldUntil = mutableMapOf<String, Long>()
    private val pendingWrites = LinkedHashMap<String, () -> Unit>()
    private var flushScheduled = false
    private val flushRunnable = Runnable { flushWrites() }

    private class DialCell(val root: View, val label: TextView, val dial: DialView, val value: TextView)

    private val cells = mutableListOf<DialCell>()

    // Which parameter each cell is showing right now. Swapped wholesale
    // when the section changes; the dial callbacks read through it.
    private var cellParams: List<DynParam> = DynParam.comp

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityDynamicsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        enterFullScreen()

        channel = intent.getIntExtra(EXTRA_CHANNEL, 0)
        dynamics = ChannelDynamics(channel)
        compOnly = intent.getBooleanExtra(EXTRA_COMP, true)
        compSelected = compOnly || savedInstanceState?.getBoolean(STATE_COMP) ?: false

        binding.channelNumber.text = channel.toString()
        binding.channelName.text = intent.getStringExtra(EXTRA_NAME) ?: "Ch $channel"

        binding.backButton.setOnClickListener { finish() }
        if (compOnly) {
            // A heading rather than a switch: Dynamics 1 has no gate.
            binding.compChip.text = "DYNAMICS 1"
            binding.compChip.isClickable = false
            binding.gateChip.visibility = View.GONE
        } else {
            binding.compChip.setOnClickListener { select(true) }
            binding.gateChip.setOnClickListener { select(false) }
        }
        binding.inButton.setOnClickListener { toggleIn() }

        binding.dynGraph.compOnly = compOnly
        binding.dynGraph.onSelect = { comp -> select(comp) }
        binding.dynGraph.onThreshold = { comp, db ->
            setValue(if (comp) DynParam.COMP_THRESH else DynParam.GATE_THRESH, db)
        }
        binding.dynGraph.onGestureEnd = { flushWrites() }

        buildDials()
        render()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(STATE_COMP, compSelected)
    }

    override fun onResume() {
        super.onResume()
        enterFullScreen()

        if (!MixerClient.isConnected) {
            returnToLogin()
            return
        }

        MixerClient.claimListener(this)

        // Mixer mode first - select_mixer clears the open panel - then
        // this channel's dynamics. See EqActivity.onResume.
        MixerClient.selectMixerControl()
        MixerClient.selectDynamics(channel)
    }

    override fun onPause() {
        super.onPause()
        flushWrites()

        if (MixerClient.isConnected) MixerClient.selectDynamics(null)
        MixerClient.releaseListener(this)
    }

    private fun enterFullScreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)

        val controller = WindowInsetsControllerCompat(window, binding.root)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    // MARK: - Dials

    /** Five cells, three over two, with an empty slot keeping the bottom
     *  row's dials the same width as the top's. */
    private fun buildDials() {
        repeat(5) { index ->
            val row = if (index < 3) binding.dialRowTop else binding.dialRowBottom
            val root = layoutInflater.inflate(R.layout.item_dyn_dial, row, false)
            row.addView(root)

            val cell = DialCell(
                root,
                root.findViewById(R.id.dial_label),
                root.findViewById(R.id.dial),
                root.findViewById(R.id.dial_value)
            )

            cell.dial.onValueChanged = { raw ->
                val param = cellParams[index]
                setValue(param, if (param.log) 2.0.pow(raw) else raw)
            }
            cell.dial.onTurnFinished = { flushWrites() }

            cells.add(cell)
        }

        binding.dialRowBottom.addView(
            Space(this),
            android.widget.LinearLayout.LayoutParams(0, 1, 1f)
        )
    }

    // MARK: - Editing

    private fun select(comp: Boolean) {
        if (comp == compSelected) return
        compSelected = comp
        render()
    }

    private fun section(comp: Boolean) = if (comp) dynamics.comp else dynamics.gate

    private fun withSection(comp: Boolean, change: (DynSection) -> DynSection) {
        dynamics = if (comp) {
            dynamics.copy(comp = change(dynamics.comp))
        } else {
            dynamics.copy(gate = change(dynamics.gate))
        }
    }

    private fun toggleIn() {
        val comp = compSelected
        val target = section(comp).isIn != true
        val key = if (comp) "comp_in" else "gate_in"

        hold(key, TOGGLE_HOLD_MS)
        withSection(comp) { it.copy(isIn = target) }
        queueWrite(key) {
            if (comp) MixerClient.setCompIn(channel, target) else MixerClient.setGateIn(channel, target)
        }
        flushWrites()
        render()
    }

    // Refuses a value the desk has not reported: there is nothing to move
    // from, and a drag would put an invented value on the console.
    private fun setValue(param: DynParam, value: Double) {
        if (section(param.isComp).values[param.key] == null) return

        val clamped = value.coerceIn(param.range)
        hold(param.wireName, DRAG_HOLD_MS)
        withSection(param.isComp) { it.copy(values = it.values + (param.key to clamped)) }
        queueWrite(param.wireName) { MixerClient.setDynamics(channel, param, clamped) }
        render()
    }

    private fun hold(key: String, millis: Long) {
        heldUntil[key] = SystemClock.uptimeMillis() + millis
    }

    private fun queueWrite(key: String, write: () -> Unit) {
        pendingWrites[key] = write

        if (!flushScheduled) {
            flushScheduled = true
            handler.postDelayed(flushRunnable, WRITE_INTERVAL_MS)
        }
    }

    private fun flushWrites() {
        handler.removeCallbacks(flushRunnable)
        flushScheduled = false

        val writes = pendingWrites.values.toList()
        pendingWrites.clear()
        writes.forEach { it() }
    }

    /** The desk's frame, keeping the phone's own value for anything held. */
    private fun merge(incoming: ChannelDynamics) {
        val now = SystemClock.uptimeMillis()
        fun held(key: String) = (heldUntil[key] ?: 0L) > now

        fun mergeSection(comp: Boolean, from: DynSection, local: DynSection): DynSection {
            val prefix = if (comp) "comp_" else "gate_"
            val values = from.values.toMutableMap()

            for ((key, value) in local.values) {
                if (held(prefix + key)) values[key] = value
            }

            return from.copy(
                isIn = if (held(prefix + "in")) local.isIn else from.isIn,
                values = values
            )
        }

        dynamics = ChannelDynamics(
            channel = channel,
            comp = mergeSection(true, incoming.comp, dynamics.comp),
            gate = mergeSection(false, incoming.gate, dynamics.gate)
        )
    }

    // MARK: - Drawing

    private fun render() {
        binding.dynGraph.dynamics = dynamics
        binding.dynGraph.compSelected = compSelected

        val compColor = ContextCompat.getColor(this, R.color.eq_band_lf)
        val gateColor = ContextCompat.getColor(this, R.color.eq_band_hmf)

        tint(binding.compChip, compSelected, compColor)
        tint(binding.gateChip, !compSelected, gateColor)

        val section = section(compSelected)
        tint(binding.inButton, section.isIn == true, if (compSelected) compColor else gateColor)

        binding.modeLabel.text = modeText(section)
        binding.modeLabel.visibility = if (binding.modeLabel.text.isEmpty()) View.GONE else View.VISIBLE

        cellParams = if (compSelected) DynParam.comp else DynParam.gate

        cellParams.forEachIndexed { index, param ->
            val cell = cells[index]
            val value = section.values[param.key]

            cell.label.text = param.label
            cell.dial.min = if (param.log) log2(param.range.start) else param.range.start
            cell.dial.max = if (param.log) log2(param.range.endInclusive) else param.range.endInclusive
            cell.dial.rate = param.rate
            cell.dial.hasValue = value != null
            value?.let { cell.dial.value = if (param.log) log2(it) else it }
            cell.value.text = param.format(value)
        }
    }

    /**
     * Names the mode when it is anything but the plain one the dials are
     * drawn for. The names follow the order the console's own parameter
     * names list them in (comp-multiband-desser, gate-duck-comp). The
     * compressor's are confirmed (channel 43, 2026-10-24: 1 multiband, 2
     * de-esser); the gate slot's are not yet.
     */
    private fun modeText(section: DynSection): String {
        val mode = section.mode ?: return ""
        if (mode == 0) return ""

        val name = if (compSelected) {
            when (mode) { 1 -> "multiband"; 2 -> "de-esser"; else -> "mode $mode" }
        } else {
            when (mode) { 1 -> "duck"; 2 -> "compressor"; else -> "mode $mode" }
        }

        return "Console is running this section as $name - its first-band " +
            "settings are shown. Change the mode on the desk."
    }

    private fun tint(button: MaterialButton, lit: Boolean, litColor: Int) {
        button.backgroundTintList = ColorStateList.valueOf(
            if (lit) litColor else ContextCompat.getColor(this, R.color.mute_inactive)
        )
        button.setTextColor(
            if (lit) android.graphics.Color.WHITE
            else ContextCompat.getColor(this, R.color.on_mute_inactive)
        )
    }

    private fun returnToLogin() {
        val intent = Intent(this, ConnectActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        startActivity(intent)
    }

    // MARK: - MixerClientListener

    override fun onDynamics(dynamics: ChannelDynamics) {
        if (dynamics.channel != channel) return
        merge(dynamics)
        render()
    }

    override fun onLevels(aux: Int, channels: List<ChannelState>) {
        if (aux != MixerClient.MIXER_AUX) return
        channels.firstOrNull { it.channel == channel }?.let {
            binding.channelName.text = it.name
        }
    }

    override fun onDisconnected() {
        returnToLogin()
    }

    override fun onConnectionFailed(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        returnToLogin()
    }

    override fun onError(message: String) {
        binding.statusLabel.text = message
    }

    companion object {
        fun start(context: Context, channel: ChannelState, comp: Boolean) {
            context.startActivity(
                Intent(context, DynamicsActivity::class.java)
                    .putExtra(EXTRA_CHANNEL, channel.channel)
                    .putExtra(EXTRA_NAME, channel.name)
                    .putExtra(EXTRA_COMP, comp)
            )
        }

        // The same timings as the EQ screen - see EqActivity.
        private const val WRITE_INTERVAL_MS = 40L
        private const val DRAG_HOLD_MS = 400L
        private const val TOGGLE_HOLD_MS = 2000L
    }
}
