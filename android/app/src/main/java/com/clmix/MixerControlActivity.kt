package com.clmix

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.LinearLayoutManager
import com.clmix.databinding.ActivityMixerControlBinding

private const val STATE_BANK = "bank"
private const val STATE_HARD_MUTE = "hardMute"

// As MixerActivity: four bank buttons a row, and a panel animation short
// enough to read as a control rather than a transition.
private const val BANK_COLUMNS = 4
private const val BANK_PANEL_ANIM_MS = 100L

/**
 * Full Mixer Control: the console's own channel faders, pans and mutes.
 *
 * The same strips as MixerActivity, riding different parameters - the
 * socket is put into mixer mode on resume (MixerClient.selectMixerControl)
 * and from then on every level/pan/mute write the adapter makes lands on
 * the channel itself rather than on one aux's sends. Reached only from
 * ControlChoiceActivity, which is itself only offered to accounts holding
 * the permission; the server checks it again on every write.
 *
 * What is deliberately absent, because it belongs to mixing one send:
 * the aux picker sheet and presets.
 */
class MixerControlActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityMixerControlBinding
    private lateinit var adapter: ChannelAdapter
    private lateinit var bankAdapter: BankAdapter

    private var selectedBank: String? = null
    private var smoothEnabled = false
    private val draggingChannels = mutableSetOf<Int>()
    private val dragReleasedAt = mutableMapOf<Int, Long>()
    private var bankPanelAnimator: ValueAnimator? = null
    private var panSheet: PanBottomSheet? = null
    private var inputSheet: ChannelInputBottomSheet? = null

    // Session-only, and off on every fresh entry to this screen: hard
    // mute reaches every monitor mix, so it should be something the
    // operator turns on for a reason rather than something left on from
    // last time. Carried across a rebuild (a theme switch, a rotation)
    // so it does not drop out from under a hand mid-show.
    private var hardMute = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMixerControlBinding.inflate(layoutInflater)
        setContentView(binding.root)
        enterFullScreen()

        selectedBank = savedInstanceState?.getString(STATE_BANK)
        hardMute = savedInstanceState?.getBoolean(STATE_HARD_MUTE) ?: false

        adapter = ChannelAdapter(
            onLevelChanged = { channel, db -> MixerClient.setLevel(channel, db) },
            onDragStart = { channel -> draggingChannels.add(channel) },
            onDragEnd = { channel ->
                draggingChannels.remove(channel)
                dragReleasedAt[channel] = System.currentTimeMillis()
            },
            onPanButtonClicked = { channel -> showPanSheet(channel) },
            onMuteToggled = { channel, muted ->
                MixerClient.setMute(channel, muted, hard = hardMute)
            },
            onChannelNumberClicked = { channel -> showChannelInputSheet(channel) }
        )

        // Both are the console's own controls, and Full Mixer Control is
        // the single permission covering them - unlike the aux screen,
        // where Mute is gated separately and Pan depends on the bus being
        // stereo. A mono channel still reports no pan value, and the
        // strip drops the control for that channel on its own.
        adapter.muteSupported = true
        adapter.panSupported = true

        // The console's own numbering, above each name - see
        // ChannelAdapter.showChannelNumber.
        adapter.showChannelNumber = true
        adapter.hardMute = hardMute

        binding.channelRecycler.layoutManager =
            LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        binding.channelRecycler.adapter = adapter

        // See MixerActivity: the bars hide asynchronously, after the
        // first layout, so strips measured before that are the wrong
        // height until they are asked to measure again.
        binding.channelRecycler.addOnLayoutChangeListener { _, _, top, _, bottom, _, oldTop, _, oldBottom ->
            if (bottom - top != oldBottom - oldTop) {
                adapter.syncAllFaderWidths(binding.channelRecycler)

                binding.channelRecycler.post {
                    for (i in 0 until binding.channelRecycler.childCount) {
                        binding.channelRecycler.getChildAt(i).requestLayout()
                    }
                }
            }
        }

        bankAdapter = BankAdapter { bank ->
            setBanksExpanded(false)

            if (bank != selectedBank) {
                selectedBank = bank
                bankAdapter.selectedBank = bank
                MixerClient.selectBank(bank)
            }
        }

        binding.bankRecycler.layoutManager = GridLayoutManager(this, BANK_COLUMNS)
        binding.bankRecycler.adapter = bankAdapter

        binding.bankToggle.setOnClickListener {
            setBanksExpanded(binding.bankRecycler.visibility != View.VISIBLE)
        }

        binding.menuButton.setOnClickListener {
            binding.drawerLayout.openDrawer(GravityCompat.START)
        }

        // Checked state before the listener, as on the aux screen: the
        // assignment fires it, which would otherwise record a preference
        // the user never expressed.
        binding.darkModeSwitch.isChecked = ThemeStore.isDarkMode(this)
        binding.darkModeSwitch.setOnCheckedChangeListener { _, isChecked ->
            ThemeStore.setDarkMode(this, isChecked)
        }

        // Checked state before the listener, as with Dark mode: the
        // assignment fires it, and a rebuild would otherwise replay the
        // restored state as a fresh tap.
        binding.hardMuteSwitch.isChecked = hardMute
        binding.hardMuteSwitch.setOnCheckedChangeListener { _, isChecked ->
            hardMute = isChecked
            adapter.hardMute = isChecked
        }

        // Back to the AUX Only / Mixer Control choice, which is still
        // underneath in the back stack - finishing returns to it rather
        // than building a second copy. The session stays up; only this
        // screen ends.
        binding.backButton.setOnClickListener {
            binding.drawerLayout.closeDrawer(GravityCompat.START)
            dismissPanSheet()
            dismissInputSheet()
            finish()
        }

        binding.logoutButton.setOnClickListener { logout() }

        binding.smoothButton.setOnClickListener {
            smoothEnabled = !smoothEnabled
            adapter.smoothEnabled = smoothEnabled
            updateSmoothButtonAppearance()
        }
        updateSmoothButtonAppearance()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(STATE_BANK, selectedBank)
        outState.putBoolean(STATE_HARD_MUTE, hardMute)
    }

    override fun onResume() {
        super.onResume()
        enterFullScreen()

        if (!MixerClient.isConnected) {
            returnToLogin()
            return
        }

        MixerClient.claimListener(this)

        // Re-asserted on every resume rather than once in onCreate: the
        // socket is shared with the other screens, and coming back here
        // from one of them (or after a rebuild) must put it back into
        // mixer mode before any fader on screen is touched.
        MixerClient.selectMixerControl()
        MixerClient.requestBanks()
    }

    override fun onPause() {
        super.onPause()
        MixerClient.releaseListener(this)
    }

    private fun enterFullScreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)

        val controller = WindowInsetsControllerCompat(window, binding.root)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }

    private fun showPanSheet(channel: ChannelState) {
        val sheet = PanBottomSheet(
            this, channel.channel, channel.name, channel.pan ?: 0.0
        ) { ch, pan -> MixerClient.setPan(ch, pan) }

        sheet.setOnDismissListener {
            if (panSheet === sheet) panSheet = null
        }

        panSheet = sheet
        sheet.show()
    }

    private fun dismissPanSheet() {
        panSheet?.dismiss()
        panSheet = null
    }

    private fun showChannelInputSheet(channel: ChannelState) {
        val sheet = ChannelInputBottomSheet(
            context = this,
            channel = channel.channel,
            channelName = channel.name,
            gain = channel.gain,
            trim = channel.trim,
            phantom = channel.phantom,
            phase = channel.phase,
            onGainChanged = { ch, gain -> MixerClient.setGain(ch, gain) },
            onTrimChanged = { ch, trim -> MixerClient.setTrim(ch, trim) },
            onPhantomChanged = { ch, on -> MixerClient.setPhantom(ch, on) },
            onPhaseChanged = { ch, on -> MixerClient.setPhase(ch, on) },
            onNameChanged = { ch, name -> MixerClient.setName(ch, name) },
            onInputClicked = { showInputPicker() }
        )

        sheet.setOnDismissListener {
            if (inputSheet === sheet) inputSheet = null
        }

        inputSheet = sheet
        sheet.show()
    }

    // The rack/port patch itself is not in the console's OSC address
    // space as probed (see docs/mixer_protocol) - nothing under
    // Channel_Input names a socket - so the button says so rather than
    // pretending to route something.
    private fun showInputPicker() {
        Toast.makeText(
            this,
            "Input patching isn't available from the console yet",
            Toast.LENGTH_SHORT
        ).show()
    }

    private fun dismissInputSheet() {
        inputSheet?.dismiss()
        inputSheet = null
    }

    private fun updateSmoothButtonAppearance() {
        val backgroundRes = if (smoothEnabled) R.color.secondary else R.color.mute_inactive
        val textRes = if (smoothEnabled) R.color.on_secondary else R.color.on_mute_inactive

        binding.smoothButton.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(this, backgroundRes)
        )
        binding.smoothButton.setTextColor(ContextCompat.getColor(this, textRes))
    }

    /** Identical to MixerActivity's panel, minus the aux sheet it also closed. */
    private fun setBanksExpanded(expanded: Boolean) {
        val panel = binding.bankRecycler
        val alreadyThere = (panel.visibility == View.VISIBLE) == expanded

        if (alreadyThere && bankPanelAnimator == null) return

        bankPanelAnimator?.cancel()

        binding.bankToggle.animate()
            .rotation(if (expanded) 180f else 0f)
            .setDuration(BANK_PANEL_ANIM_MS)
            .start()
        binding.bankToggle.contentDescription =
            if (expanded) "Hide banks" else "Show banks"

        val from = if (panel.visibility == View.VISIBLE) panel.height else 0
        val to = if (expanded) measureBankPanelHeight(panel) else 0

        if (to == from) {
            finishBankPanel(panel, expanded)
            return
        }

        panel.visibility = View.VISIBLE

        bankPanelAnimator = ValueAnimator.ofInt(from, to).apply {
            duration = BANK_PANEL_ANIM_MS
            interpolator = DecelerateInterpolator()

            addUpdateListener { anim ->
                panel.layoutParams.height = anim.animatedValue as Int
                panel.requestLayout()
            }

            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    bankPanelAnimator = null
                    finishBankPanel(panel, expanded)
                }
            })

            start()
        }
    }

    private fun finishBankPanel(panel: View, expanded: Boolean) {
        panel.layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
        panel.visibility = if (expanded) View.VISIBLE else View.GONE
        panel.requestLayout()
    }

    private fun measureBankPanelHeight(panel: View): Int {
        val available = (panel.parent as? View)?.width ?: return 0

        panel.measure(
            View.MeasureSpec.makeMeasureSpec(available, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        )

        return panel.measuredHeight
    }

    private fun logout() {
        MixerClient.logout(SessionStore.getToken(this))
        SessionStore.clear(this)
        MixerClient.disconnect()
        returnToLogin()
    }

    private fun returnToLogin() {
        dismissPanSheet()
        dismissInputSheet()

        val intent = Intent(this, ConnectActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        startActivity(intent)
    }

    override fun onMeters(sequence: Long, meters: Map<Int, MeterLevels>) {
        adapter.updateMeters(sequence, meters, binding.channelRecycler)
    }

    override fun onDisconnected() {
        binding.statusLabel.text = "Disconnected"
        returnToLogin()
    }

    override fun onConnectionFailed(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        returnToLogin()
    }

    override fun onError(message: String) {
        binding.statusLabel.text = message
    }

    override fun onBanks(banks: List<String>) {
        bankAdapter.submit(banks)

        if (banks.isEmpty()) {
            // A console reporting no banks: fall back to every channel,
            // exactly as the aux screen does.
            if (selectedBank != null) {
                selectedBank = null
                bankAdapter.selectedBank = null
                MixerClient.selectBank(null)
            }

            setBanksExpanded(false)
            return
        }

        val bank = selectedBank.takeIf { it in banks } ?: banks.first()

        if (bank != selectedBank) {
            selectedBank = bank
            MixerClient.selectBank(bank)
        }

        bankAdapter.selectedBank = bank
    }

    override fun onLevels(aux: Int, channels: List<ChannelState>) {
        // Anything still arriving for a bus belongs to the aux screen -
        // most likely a frame already in flight when the socket switched
        // modes, which would otherwise paint send levels onto these
        // console faders for one frame.
        if (aux != MixerClient.MIXER_AUX) return

        adapter.updateChannels(channels, draggingChannels, dragReleasedAt)

        panSheet?.let { sheet ->
            channels.firstOrNull { it.channel == sheet.channel }?.pan?.let(sheet::updateIfIdle)
        }

        inputSheet?.let { sheet ->
            channels.firstOrNull { it.channel == sheet.channel }?.let(sheet::update)
        }
    }
}
