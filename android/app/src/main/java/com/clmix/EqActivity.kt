package com.clmix

import android.content.Context
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.clmix.databinding.ActivityEqBinding
import com.google.android.material.button.MaterialButton
import kotlin.math.log2
import kotlin.math.pow

private const val EXTRA_CHANNEL = "channel"
private const val EXTRA_NAME = "name"
private const val STATE_SELECTED = "selected"

/**
 * One input channel's EQ, full screen and turned on its side.
 *
 * Opened from the EQ button on a Full Mixer Control strip, and like that
 * screen it reaches the channel itself - every curve drawn here is what
 * FOH, the monitors and the recording all hear. The socket stays in mixer
 * mode underneath; this screen only adds select_eq, which has the server
 * follow each levels push with an "eq" frame for this one channel.
 *
 * Landscape because the frequency axis wants the width: the manifest
 * fixes it, and the mixer underneath rotates back when this finishes.
 *
 * Two ways to change a band, which always agree: drag its handle on the
 * graph (frequency across, gain up and down, pinch for Q), or turn the
 * selected band's dials. Both edit [eq] here first so the picture moves
 * under the finger, then write to the desk at a capped rate. While a
 * value is being moved - and briefly after - the desk's echo of it is
 * ignored, the same grace the faders give a drag, so an in-flight echo
 * of an older position cannot pull a handle back.
 */
class EqActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityEqBinding

    private var channel = 0
    private var eq = ChannelEq(0)
    private var selected: EqTarget = EqTarget.Band(EqResponse.LOW_BAND)

    private val handler = Handler(Looper.getMainLooper())

    // Per value (see keyFor*), when the desk's own reading of it is
    // trusted again.
    private val heldUntil = mutableMapOf<String, Long>()

    // The latest write per value, sent at most every WRITE_INTERVAL_MS. A
    // drag on a 120 Hz screen would otherwise put over a hundred UDP
    // packets a second on the console's link for one handle.
    private val pendingWrites = LinkedHashMap<String, () -> Unit>()
    private var flushScheduled = false
    private val flushRunnable = Runnable { flushWrites() }

    private val targets: List<EqTarget> =
        listOf<EqTarget>(EqTarget.LoFilter) +
            EqResponse.bandsLowToHigh.map { EqTarget.Band(it) } +
            EqTarget.HiFilter

    private val chips = mutableMapOf<EqTarget, MaterialButton>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityEqBinding.inflate(layoutInflater)
        setContentView(binding.root)
        enterFullScreen()

        channel = intent.getIntExtra(EXTRA_CHANNEL, 0)
        eq = ChannelEq(channel)

        savedInstanceState?.getString(STATE_SELECTED)?.let { decodeTarget(it) }?.let {
            selected = it
        }

        binding.channelNumber.text = channel.toString()
        binding.channelName.text = intent.getStringExtra(EXTRA_NAME) ?: "Ch $channel"

        binding.backButton.setOnClickListener { finish() }

        binding.eqInButton.setOnClickListener {
            val target = eq.eqIn != true
            hold(KEY_EQ_IN, TOGGLE_HOLD_MS)
            eq = eq.copy(eqIn = target)
            queueWrite(KEY_EQ_IN) { MixerClient.setEqIn(channel, target) }
            flushWrites()
            render()
        }

        buildChips()

        binding.targetOnButton.setOnClickListener { toggleSelected() }

        binding.eqGraph.onSelect = { target -> select(target) }
        binding.eqGraph.onDrag = { target, hz, db ->
            setFreq(target, hz)
            if (target is EqTarget.Band && db != null) setGain(target.band, db)
        }
        binding.eqGraph.onPinch = { band, q -> setQ(band, q) }
        binding.eqGraph.onGestureEnd = { flushWrites() }

        // Frequency and Q turn in octaves rather than in Hz: a dial
        // sweeping 20..20000 linearly would spend nearly all its travel
        // above 1 kHz. The rates are per unit - an octave, a dB - against
        // DialView's own speed curve.
        binding.freqDial.min = log2(EQ_FREQ_RANGE.start)
        binding.freqDial.max = log2(EQ_FREQ_RANGE.endInclusive)
        binding.freqDial.rate = FREQ_DIAL_RATE
        binding.freqDial.onValueChanged = { octaves -> setFreq(selected, 2.0.pow(octaves)) }
        binding.freqDial.onTurnFinished = { flushWrites() }

        binding.gainDial.min = EQ_GAIN_RANGE.start
        binding.gainDial.max = EQ_GAIN_RANGE.endInclusive
        binding.gainDial.rate = GAIN_DIAL_RATE
        binding.gainDial.onValueChanged = { db ->
            (selected as? EqTarget.Band)?.let { setGain(it.band, db) }
        }
        binding.gainDial.onTurnFinished = { flushWrites() }

        binding.qDial.min = log2(EQ_Q_RANGE.start)
        binding.qDial.max = log2(EQ_Q_RANGE.endInclusive)
        binding.qDial.rate = Q_DIAL_RATE
        binding.qDial.onValueChanged = { octaves ->
            (selected as? EqTarget.Band)?.let { setQ(it.band, 2.0.pow(octaves)) }
        }
        binding.qDial.onTurnFinished = { flushWrites() }

        render()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(STATE_SELECTED, encodeTarget(selected))
    }

    override fun onResume() {
        super.onResume()
        enterFullScreen()

        if (!MixerClient.isConnected) {
            returnToLogin()
            return
        }

        MixerClient.claimListener(this)

        // Mixer mode first, then the EQ: select_mixer clears any EQ the
        // socket had open, so the other order would close this one
        // straight away. Re-asserted on every resume, as Full Mixer
        // Control does, since the socket is shared with every screen.
        MixerClient.selectMixerControl()
        MixerClient.selectEq(channel)
    }

    override fun onPause() {
        super.onPause()

        // Anything still waiting goes now - leaving mid-drag must not
        // strand the last position on the phone.
        flushWrites()

        if (MixerClient.isConnected) MixerClient.selectEq(null)
        MixerClient.releaseListener(this)
    }

    private fun enterFullScreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)

        val controller = WindowInsetsControllerCompat(window, binding.root)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    // MARK: - Editing

    private fun select(target: EqTarget) {
        if (target == selected) return
        selected = target
        render()
    }

    private fun toggleSelected() {
        when (val target = selected) {
            is EqTarget.Band -> {
                val band = eq.bands[target.band - 1]
                val on = band.on != true
                val key = keyFor("on", target.band)
                hold(key, TOGGLE_HOLD_MS)
                updateBand(target.band) { it.copy(on = on) }
                queueWrite(key) { MixerClient.setEqBandOn(channel, target.band, on) }
            }

            EqTarget.LoFilter, EqTarget.HiFilter -> {
                val lo = target == EqTarget.LoFilter
                val filter = if (lo) eq.loFilter else eq.hiFilter
                val isIn = filter.isIn != true
                val key = if (lo) KEY_LO_IN else KEY_HI_IN
                hold(key, TOGGLE_HOLD_MS)
                eq = if (lo) {
                    eq.copy(loFilter = filter.copy(isIn = isIn))
                } else {
                    eq.copy(hiFilter = filter.copy(isIn = isIn))
                }
                queueWrite(key) { MixerClient.setFilterIn(channel, lo, isIn) }
            }
        }

        flushWrites()
        render()
    }

    private fun setFreq(target: EqTarget, hz: Double) {
        val clamped = hz.coerceIn(EQ_FREQ_RANGE)

        when (target) {
            is EqTarget.Band -> {
                if (eq.bands[target.band - 1].freq == null) return
                val key = keyFor("freq", target.band)
                hold(key, DRAG_HOLD_MS)
                updateBand(target.band) { it.copy(freq = clamped) }
                queueWrite(key) { MixerClient.setEqFreq(channel, target.band, clamped) }
            }

            EqTarget.LoFilter -> {
                if (eq.loFilter.freq == null) return
                hold(KEY_LO_FREQ, DRAG_HOLD_MS)
                eq = eq.copy(loFilter = eq.loFilter.copy(freq = clamped))
                queueWrite(KEY_LO_FREQ) { MixerClient.setFilterFreq(channel, true, clamped) }
            }

            EqTarget.HiFilter -> {
                if (eq.hiFilter.freq == null) return
                hold(KEY_HI_FREQ, DRAG_HOLD_MS)
                eq = eq.copy(hiFilter = eq.hiFilter.copy(freq = clamped))
                queueWrite(KEY_HI_FREQ) { MixerClient.setFilterFreq(channel, false, clamped) }
            }
        }

        render()
    }

    private fun setGain(band: Int, db: Double) {
        if (eq.bands[band - 1].freq == null) return

        val clamped = db.coerceIn(EQ_GAIN_RANGE)
        val key = keyFor("gain", band)
        hold(key, DRAG_HOLD_MS)
        updateBand(band) { it.copy(gain = clamped) }
        queueWrite(key) { MixerClient.setEqGain(channel, band, clamped) }
        render()
    }

    private fun setQ(band: Int, q: Double) {
        if (eq.bands[band - 1].q == null) return

        val clamped = q.coerceIn(EQ_Q_RANGE)
        val key = keyFor("q", band)
        hold(key, DRAG_HOLD_MS)
        updateBand(band) { it.copy(q = clamped) }
        queueWrite(key) { MixerClient.setEqQ(channel, band, clamped) }
        render()
    }

    private fun updateBand(band: Int, change: (EqBand) -> EqBand) {
        eq = eq.copy(bands = eq.bands.map { if (it.band == band) change(it) else it })
    }

    private fun hold(key: String, millis: Long) {
        heldUntil[key] = SystemClock.uptimeMillis() + millis
    }

    private fun isHeld(key: String, now: Long): Boolean = (heldUntil[key] ?: 0L) > now

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

    /**
     * Folds a frame from the desk into what is on screen, keeping the
     * phone's own value for anything it is still holding (see heldUntil).
     */
    private fun merge(incoming: ChannelEq) {
        val now = SystemClock.uptimeMillis()

        val bands = incoming.bands.map { from ->
            val local = eq.bands.firstOrNull { it.band == from.band } ?: from
            val n = from.band

            EqBand(
                band = n,
                on = if (isHeld(keyFor("on", n), now)) local.on else from.on,
                freq = if (isHeld(keyFor("freq", n), now)) local.freq else from.freq,
                gain = if (isHeld(keyFor("gain", n), now)) local.gain else from.gain,
                q = if (isHeld(keyFor("q", n), now)) local.q else from.q,
                curve = from.curve
            )
        }

        eq = ChannelEq(
            channel = channel,
            eqIn = if (isHeld(KEY_EQ_IN, now)) eq.eqIn else incoming.eqIn,
            bands = bands,
            loFilter = EqFilter(
                isIn = if (isHeld(KEY_LO_IN, now)) eq.loFilter.isIn else incoming.loFilter.isIn,
                freq = if (isHeld(KEY_LO_FREQ, now)) eq.loFilter.freq else incoming.loFilter.freq
            ),
            hiFilter = EqFilter(
                isIn = if (isHeld(KEY_HI_IN, now)) eq.hiFilter.isIn else incoming.hiFilter.isIn,
                freq = if (isHeld(KEY_HI_FREQ, now)) eq.hiFilter.freq else incoming.hiFilter.freq
            )
        )
    }

    // MARK: - Drawing

    private fun buildChips() {
        val row = binding.targetRow

        for (target in targets) {
            val chip = layoutInflater.inflate(R.layout.item_eq_target, row, false) as MaterialButton
            chip.text = chipLabel(target)
            chip.setOnClickListener { select(target) }
            row.addView(chip)
            chips[target] = chip
        }
    }

    private fun render() {
        binding.eqGraph.eq = eq
        binding.eqGraph.selected = selected

        tint(
            binding.eqInButton, eq.eqIn == true,
            ContextCompat.getColor(this, R.color.primary),
            ContextCompat.getColor(this, R.color.on_primary)
        )
        binding.eqInButton.text = if (eq.eqIn == false) "EQ OUT" else "EQ IN"

        for ((target, chip) in chips) {
            tint(chip, target == selected, eqTargetColor(this, target))
        }

        val color = eqTargetColor(this, selected)

        when (val target = selected) {
            is EqTarget.Band -> {
                val band = eq.bands[target.band - 1]
                binding.targetTitle.text = "${EqResponse.label(target.band)} - Band ${target.band}"
                binding.targetOnButton.text = "ON"
                tint(binding.targetOnButton, band.on == true, color)

                binding.gainColumn.visibility = View.VISIBLE
                binding.qColumn.visibility = View.VISIBLE

                showFreq(band.freq)

                binding.gainDial.hasValue = band.gain != null
                band.gain?.let { binding.gainDial.value = it }
                binding.gainValue.text = formatEqGain(band.gain)

                binding.qDial.hasValue = band.q != null
                band.q?.let { binding.qDial.value = log2(it) }
                binding.qValue.text = formatEqQ(band.q)
            }

            EqTarget.LoFilter, EqTarget.HiFilter -> {
                val lo = target == EqTarget.LoFilter
                val filter = if (lo) eq.loFilter else eq.hiFilter
                binding.targetTitle.text = if (lo) "High-pass filter" else "Low-pass filter"
                binding.targetOnButton.text = "IN"
                tint(binding.targetOnButton, filter.isIn == true, color)

                // Kept in the layout, just not shown, so the frequency
                // dial does not jump sideways between a band and a filter.
                binding.gainColumn.visibility = View.INVISIBLE
                binding.qColumn.visibility = View.INVISIBLE

                showFreq(filter.freq)
            }
        }
    }

    private fun showFreq(hz: Double?) {
        binding.freqDial.hasValue = hz != null
        hz?.let { binding.freqDial.value = log2(it) }
        binding.freqValue.text = formatEqFreq(hz)
    }

    // Band and filter colours are mid-brightness in both palettes, so
    // white reads on every one of them; EQ IN passes on_primary instead,
    // which is dark on the night palette's lighter blue.
    private fun tint(
        button: MaterialButton,
        lit: Boolean,
        litColor: Int,
        litText: Int = Color.WHITE
    ) {
        button.backgroundTintList = ColorStateList.valueOf(
            if (lit) litColor else ContextCompat.getColor(this, R.color.mute_inactive)
        )
        button.setTextColor(
            if (lit) litText else ContextCompat.getColor(this, R.color.on_mute_inactive)
        )
    }

    private fun chipLabel(target: EqTarget): String = when (target) {
        is EqTarget.Band -> EqResponse.label(target.band)
        EqTarget.LoFilter -> "HPF"
        EqTarget.HiFilter -> "LPF"
    }

    // MARK: - Leaving

    private fun returnToLogin() {
        val intent = Intent(this, ConnectActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        startActivity(intent)
    }

    // MARK: - MixerClientListener

    override fun onEq(eq: ChannelEq) {
        if (eq.channel != channel) return
        merge(eq)
        render()
    }

    override fun onLevels(aux: Int, channels: List<ChannelState>) {
        // Only for the name: a rename on the input sheet, or from the
        // desk, shows up here too. The strip may not be in the bank the
        // mixer underneath is on, in which case the name simply stays.
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
        fun start(context: Context, channel: ChannelState) {
            context.startActivity(
                Intent(context, EqActivity::class.java)
                    .putExtra(EXTRA_CHANNEL, channel.channel)
                    .putExtra(EXTRA_NAME, channel.name)
            )
        }

        private const val KEY_EQ_IN = "eq_in"
        private const val KEY_LO_IN = "lo_in"
        private const val KEY_LO_FREQ = "lo_freq"
        private const val KEY_HI_IN = "hi_in"
        private const val KEY_HI_FREQ = "hi_freq"

        private fun keyFor(param: String, band: Int) = "$param:$band"

        private fun encodeTarget(target: EqTarget): String = when (target) {
            is EqTarget.Band -> "band:${target.band}"
            EqTarget.LoFilter -> "lo"
            EqTarget.HiFilter -> "hi"
        }

        private fun decodeTarget(text: String): EqTarget? = when {
            text == "lo" -> EqTarget.LoFilter
            text == "hi" -> EqTarget.HiFilter
            text.startsWith("band:") -> text.removePrefix("band:").toIntOrNull()
                ?.takeIf { it in 1..EQ_BAND_COUNT }
                ?.let { EqTarget.Band(it) }
            else -> null
        }

        private const val WRITE_INTERVAL_MS = 40L

        // How long the desk's echo is ignored after a value last moved:
        // the faders' DRAG_GRACE_MS plus room for a write still waiting
        // in the throttle above.
        private const val DRAG_HOLD_MS = 400L

        // A toggle waits longer, as the strips' Mute does - there is no
        // further movement coming to refresh it.
        private const val TOGGLE_HOLD_MS = 2000L

        private const val FREQ_DIAL_RATE = 0.25
        private const val GAIN_DIAL_RATE = 0.6
        private const val Q_DIAL_RATE = 0.2
    }
}
