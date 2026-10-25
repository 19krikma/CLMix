package com.clmix

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
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
private const val STATE_PAGE = "page"

/**
 * One input channel's Dynamics 1 or Dynamics 2, full screen and on its
 * side.
 *
 * The EQ screen's twin (EqActivity), opened from a strip's COMP button
 * for Dynamics 1 or its GATE button for Dynamics 2. The socket stays in
 * mixer mode and select_dyn has the server follow each levels push with
 * a "dynamics" frame for this channel.
 *
 * Either way the screen shows that one section, headed with the mode the
 * desk is running it in, and that mode's controls. Dynamics 1
 * (DynMode.pages, captured on channel 43, 2026-10-24): the compressor's
 * five dials; multiband's LOW, MID and HIGH bands, each with its own
 * power button, and ALL for the overall threshold, gain and crossovers;
 * or the de-esser's band with its frequency and width. Dynamics 2
 * (GateMode.pages, captured on channel 22, 2026-10-25): gate and ducker
 * share threshold, range, attack, hold and release; the compressor has
 * threshold, ratio, attack, release and gain. The graph draws the shown
 * curve, and follows the desk when the mode changes there.
 *
 * Edits move the screen at once and reach the desk at a capped rate, and
 * the desk's echo of a value being moved is ignored for a moment after,
 * exactly as on the EQ screen. The mode itself is never changed here.
 */
class DynamicsActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityDynamicsBinding

    private var channel = 0
    private var dynamics = ChannelDynamics(0)
    // Dynamics 1 (true) or 2, fixed by the button that opened the screen.
    private var compSelected = true

    // Each section's mode as last reported, and which page is shown.
    private var mode = DynMode.COMPRESSOR
    private var gateMode = GateMode.GATE
    private var page = 0
    private val tabs = mutableListOf<MaterialButton>()

    private val handler = Handler(Looper.getMainLooper())
    private val heldUntil = mutableMapOf<String, Long>()
    private val pendingWrites = LinkedHashMap<String, () -> Unit>()
    private var flushScheduled = false
    private val flushRunnable = Runnable { flushWrites() }

    private class DialCell(val root: View, val label: TextView, val dial: DialView, val value: TextView)

    private val cells = mutableListOf<DialCell>()

    // Which parameter each cell is showing right now. Swapped wholesale
    // when the section changes; the dial callbacks read through it.
    private var cellParams: List<DynParam> = DynParam.band(1)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityDynamicsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        enterFullScreen()

        channel = intent.getIntExtra(EXTRA_CHANNEL, 0)
        dynamics = ChannelDynamics(channel)
        compSelected = intent.getBooleanExtra(EXTRA_COMP, true)
        page = savedInstanceState?.getInt(STATE_PAGE) ?: 0

        binding.channelNumber.text = channel.toString()
        binding.channelName.text = intent.getStringExtra(EXTRA_NAME) ?: "Ch $channel"

        binding.backButton.setOnClickListener { finish() }
        // A heading naming the mode rather than a switch: each screen is
        // one section.
        binding.sectionChip.isClickable = false
        binding.bandPower.setOnClickListener { toggleBand() }
        binding.inButton.setOnClickListener { toggleIn() }

        binding.dynGraph.compSelected = compSelected
        binding.dynGraph.onThreshold = { comp, db ->
            setValue(if (comp) graphThresh() else DynParam.GATE_THRESH, db)
        }
        binding.dynGraph.onGestureEnd = { flushWrites() }

        buildDials()
        render()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt(STATE_PAGE, page)
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

    /** Six cells, three over three. A page with fewer dials leaves the
     *  rest invisible, which keeps every dial the same width. */
    private fun buildDials() {
        repeat(6) { index ->
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
                val param = cellParams.getOrNull(index)
                if (param != null) setValue(param, if (param.log) 2.0.pow(raw) else raw)
            }
            cell.dial.onTurnFinished = { flushWrites() }

            cells.add(cell)
        }
    }

    /** One tab per page of a mode that has more than one. */
    private fun buildTabs() {
        binding.bandTabs.removeAllViews()
        tabs.clear()

        val pages = pages()
        if (pages.size < 2) return

        pages.forEachIndexed { index, p ->
            val tab = MaterialButton(
                this, null, com.google.android.material.R.attr.materialButtonTonalStyle
            ).apply {
                text = p.tab
                textSize = 11f
                insetTop = 0
                insetBottom = 0
                minWidth = 0
                minimumWidth = 0
                setPadding(0, 0, 0, 0)
                cornerRadius = (8 * resources.displayMetrics.density).toInt()
                setOnClickListener { selectPage(index) }
            }
            val params = android.widget.LinearLayout.LayoutParams(0, android.view.ViewGroup.LayoutParams.MATCH_PARENT, 1f)
            if (index < pages.size - 1) params.marginEnd = (4 * resources.displayMetrics.density).toInt()
            binding.bandTabs.addView(tab, params)
            tabs.add(tab)
        }
    }

    // MARK: - Editing

    private fun selectPage(index: Int) {
        if (index == page) return
        page = index
        render()
    }

    /** The open section's pages in its current mode. */
    private fun pages(): List<DynMode.Page> = if (compSelected) mode.pages else gateMode.pages

    private fun currentPage(): DynMode.Page = pages().getOrNull(page) ?: pages().first()

    /** The threshold the graph's compressor handle moves. */
    private fun graphThresh(): DynParam = DynParam.band(currentPage().graphBand).first()

    private fun toggleBand() {
        val band = currentPage().band ?: return
        val key = "band_in_$band"
        val target = dynamics.comp.values[key] != 1.0

        hold("comp_$key", TOGGLE_HOLD_MS)
        withSection(true) { it.copy(values = it.values + (key to if (target) 1.0 else 0.0)) }
        queueWrite("comp_$key") { MixerClient.setBandIn(channel, band, target) }
        flushWrites()
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
        val reported = section(compSelected).mode
        val modeChanged = if (compSelected) {
            val m = DynMode.of(reported) ?: DynMode.COMPRESSOR
            (m != mode).also { mode = m }
        } else {
            val m = GateMode.of(reported) ?: GateMode.GATE
            (m != gateMode).also { gateMode = m }
        }
        if (modeChanged) page = 0
        if (modeChanged || tabs.isEmpty() && pages().size > 1) buildTabs()
        page = page.coerceIn(0, pages().size - 1)
        val shown = currentPage()

        binding.dynGraph.dynamics = if (compSelected) {
            dynamics.copy(comp = dynamics.comp.band(shown.graphBand))
        } else {
            dynamics
        }

        val compColor = ContextCompat.getColor(this, R.color.eq_band_lf)
        val gateColor = ContextCompat.getColor(this, R.color.eq_band_hmf)
        val color = if (compSelected) compColor else gateColor

        binding.sectionChip.text = when {
            reported == null -> if (compSelected) "DYNAMICS 1" else "DYNAMICS 2"
            compSelected -> mode.title
            else -> gateMode.title
        }
        tint(binding.sectionChip, true, color)

        val section = section(compSelected)
        tint(binding.inButton, section.isIn == true, color)

        binding.bandRow.visibility = if (tabs.isNotEmpty()) View.VISIBLE else View.GONE
        tabs.forEachIndexed { index, tab -> tint(tab, index == page, color) }
        val band = shown.band
        binding.bandPower.visibility = if (band != null) View.VISIBLE else View.INVISIBLE
        if (band != null) {
            tint(binding.bandPower, section.values["band_in_$band"] == 1.0, color)
        }

        cellParams = shown.params

        cells.forEachIndexed { index, cell ->
            cell.root.visibility = if (index < cellParams.size) View.VISIBLE else View.INVISIBLE
        }

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

    private fun tint(button: MaterialButton, lit: Boolean, litColor: Int) {
        button.backgroundTintList = ColorStateList.valueOf(
            if (lit) litColor else ContextCompat.getColor(this, R.color.mute_inactive)
        )
        val content = if (lit) android.graphics.Color.WHITE
            else ContextCompat.getColor(this, R.color.on_mute_inactive)
        button.setTextColor(content)
        button.iconTint = ColorStateList.valueOf(content)
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
