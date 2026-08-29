package com.clmix

import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.recyclerview.widget.LinearLayoutManager
import com.clmix.databinding.ActivityMixerBinding

private const val STATE_AUX_INDEX = "auxIndex"
private const val STATE_AUX_NAME = "auxName"
private const val STATE_BANK = "bank"

class MixerActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityMixerBinding
    private lateinit var adapter: ChannelAdapter
    private var auxIndex: Int = -1

    // Null means "All". Tracked (rather than just read off the spinner)
    // so it can be carried across the activity rebuild a light/dark
    // switch causes - the spinner's own saved state can't restore it,
    // since its adapter isn't populated until onBanks arrives well after
    // the view hierarchy is restored.
    private var selectedBank: String? = null

    private var smoothEnabled = false
    private val draggingChannels = mutableSetOf<Int>()
    private val dragReleasedAt = mutableMapOf<Int, Long>()
    private var panSheet: PanBottomSheet? = null
    private var presetSaveSheet: PresetSaveBottomSheet? = null
    private var presetLoadSheet: PresetLoadBottomSheet? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMixerBinding.inflate(layoutInflater)
        setContentView(binding.root)
        enterFullScreen()

        // Prefer the saved state over the intent extras: switching aux
        // from the drawer updates these fields but not the intent that
        // started this activity, so a rebuild (a light/dark switch, or
        // the phone's own at sunset) would otherwise silently drop the
        // user back on whichever aux they first opened.
        auxIndex = savedInstanceState?.getInt(STATE_AUX_INDEX)
            ?: intent.getIntExtra("auxIndex", -1)
        title = savedInstanceState?.getString(STATE_AUX_NAME)
            ?: intent.getStringExtra("auxName") ?: "Aux"
        selectedBank = savedInstanceState?.getString(STATE_BANK)

        @Suppress("UNCHECKED_CAST")
        val auxes = intent.getSerializableExtra("auxes") as? ArrayList<AuxBus> ?: arrayListOf()

        adapter = ChannelAdapter(
            onLevelChanged = { channel, db -> MixerClient.setLevel(channel, db) },
            onDragStart = { channel -> draggingChannels.add(channel) },
            onDragEnd = { channel ->
                draggingChannels.remove(channel)
                dragReleasedAt[channel] = System.currentTimeMillis()
            },
            onPanButtonClicked = { channel -> showPanSheet(channel) },
            onMuteToggled = { channel, muted -> MixerClient.setMute(channel, muted) }
        )

        binding.channelRecycler.layoutManager =
            LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        binding.channelRecycler.adapter = adapter

        // enterFullScreen() below hides the system bars asynchronously,
        // after this recycler's first layout pass - so its height grows
        // once they're gone. Any channel whose fader never happens to
        // rebind afterwards (see ChannelAdapter.syncAllFaderWidths) would
        // otherwise stay measured against the smaller, bars-visible
        // height forever.
        binding.channelRecycler.addOnLayoutChangeListener { _, _, top, _, bottom, _, oldTop, _, oldBottom ->
            if (bottom - top != oldBottom - oldTop) {
                adapter.syncAllFaderWidths(binding.channelRecycler)
            }
        }

        binding.bankSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(
                parent: AdapterView<*>?, view: android.view.View?, position: Int, id: Long
            ) {
                val selected = parent?.getItemAtPosition(position) as? String ?: return
                selectedBank = if (selected == "All") null else selected
                MixerClient.selectBank(selectedBank)
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        binding.menuButton.setOnClickListener {
            binding.drawerLayout.openDrawer(GravityCompat.START)
        }

        binding.drawerAuxRecycler.layoutManager = LinearLayoutManager(this)
        binding.drawerAuxRecycler.adapter = AuxAdapter(auxes) { aux -> switchAux(aux) }

        // Only accounts with Preset Access (Setup > Accounts on desktop)
        // get this button at all - the server enforces the same check
        // independently, but there's no point showing an action that
        // would just come back as an error.
        binding.presetsButton.visibility = if (MixerClient.presetsAllowed) View.VISIBLE else View.GONE
        binding.presetsButton.setOnClickListener { togglePresetsExpanded() }
        binding.presetSaveButton.setOnClickListener { showPresetSaveSheet() }
        binding.presetLoadButton.setOnClickListener { showPresetLoadSheet() }

        // Checked state first, listener second - assigning isChecked
        // fires the listener, which would otherwise save a "choice" the
        // user never made and pin the app to whatever the system theme
        // happened to be. The switch also carries saveEnabled="false" for
        // the same reason: view-state restore runs after this, with the
        // listener already attached, so a rebuild would replay its
        // restored state as a fresh user choice - pinning someone who had
        // never touched it. ThemeStore is the authority on what this
        // shows, and it is read fresh on every create anyway.
        binding.darkModeSwitch.isChecked = ThemeStore.isDarkMode(this)
        binding.darkModeSwitch.setOnCheckedChangeListener { _, isChecked ->
            ThemeStore.setDarkMode(this, isChecked)
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
        outState.putInt(STATE_AUX_INDEX, auxIndex)
        outState.putString(STATE_AUX_NAME, title?.toString())
        outState.putString(STATE_BANK, selectedBank)
    }

    private fun updateSmoothButtonAppearance() {
        val backgroundRes = if (smoothEnabled) R.color.secondary else R.color.mute_inactive
        val textRes = if (smoothEnabled) R.color.on_secondary else R.color.on_mute_inactive

        binding.smoothButton.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(this, backgroundRes)
        )
        binding.smoothButton.setTextColor(ContextCompat.getColor(this, textRes))
    }

    override fun onResume() {
        super.onResume()
        enterFullScreen()
        MixerClient.claimListener(this)
        MixerClient.selectAux(auxIndex)
        MixerClient.requestBanks()
    }

    override fun onPause() {
        super.onPause()
        MixerClient.releaseListener(this)
    }

    // Hide the status/navigation bars so the mixer screen - where every
    // pixel matters for the fader grid - uses the whole display. A swipe
    // from the edge still reveals the bars briefly if the user needs them.
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

    private fun togglePresetsExpanded() {
        binding.presetsExpanded.visibility =
            if (binding.presetsExpanded.visibility == View.VISIBLE) View.GONE else View.VISIBLE
    }

    private fun showPresetSaveSheet() {
        val sheet = PresetSaveBottomSheet(this) { name -> MixerClient.savePreset(name) }

        sheet.setOnDismissListener {
            if (presetSaveSheet === sheet) presetSaveSheet = null
        }

        presetSaveSheet = sheet
        sheet.show()
    }

    private fun showPresetLoadSheet() {
        val sheet = PresetLoadBottomSheet(this) { name -> MixerClient.loadPreset(name) }

        sheet.setOnDismissListener {
            if (presetLoadSheet === sheet) presetLoadSheet = null
        }

        presetLoadSheet = sheet
        sheet.show()

        // The sheet starts empty until this reply arrives (see onPresets),
        // rather than blocking show() on a round-trip to the server.
        MixerClient.requestPresets()
    }

    private fun switchAux(aux: AuxBus) {
        binding.drawerLayout.closeDrawer(GravityCompat.START)

        if (aux.index == auxIndex) return

        // Pan/presets are per-aux-send, so sheets left open from the
        // previous aux would otherwise keep acting against the new one.
        dismissPanSheet()
        dismissPresetSheets()

        auxIndex = aux.index
        title = aux.name
        MixerClient.selectAux(auxIndex)
    }

    private fun dismissPanSheet() {
        panSheet?.dismiss()
        panSheet = null
    }

    private fun dismissPresetSheets() {
        presetSaveSheet?.dismiss()
        presetSaveSheet = null
        presetLoadSheet?.dismiss()
        presetLoadSheet = null
    }

    private fun logout() {
        MixerClient.logout(SessionStore.getToken(this))
        SessionStore.clear(this)
        MixerClient.disconnect()
        returnToLogin()
    }

    private fun returnToLogin() {
        // A pan/preset sheet left showing would otherwise leak its window
        // once this activity is torn down by the task-clearing navigation
        // below.
        dismissPanSheet()
        dismissPresetSheets()

        val intent = Intent(this, ConnectActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        startActivity(intent)
    }

    override fun onDisconnected() {
        binding.statusLabel.text = "Disconnected"
        returnToLogin()
    }

    // The socket actually dropped (network lost, server gone, ...) -
    // nothing to show here since we're navigating away regardless.
    override fun onConnectionFailed(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        returnToLogin()
    }

    // A protocol-level rejection (e.g. "not permitted for this aux") -
    // the connection itself is still fine, so just surface it in place.
    override fun onError(message: String) {
        binding.statusLabel.text = message
    }

    override fun onBanks(banks: List<String>) {
        val items = listOf("All") + banks
        binding.bankSpinner.adapter =
            ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, items)

        // Put the spinner back on the bank that was showing before a
        // rebuild. Selecting it re-fires the listener, which re-sends
        // select_bank for the same bank - harmless, and it means the
        // server's idea of the filter always matches the spinner's.
        val index = items.indexOf(selectedBank)
        if (index > 0) {
            binding.bankSpinner.setSelection(index)
        }
    }

    override fun onLevels(aux: Int, channels: List<ChannelState>) {
        if (aux != auxIndex) return
        adapter.updateChannels(channels, draggingChannels, dragReleasedAt)

        panSheet?.let { sheet ->
            channels.firstOrNull { it.channel == sheet.channel }?.pan?.let(sheet::updateIfIdle)
        }
    }

    override fun onPresets(names: List<String>) {
        presetLoadSheet?.setPresets(names)
    }

    override fun onPresetSaved(name: String) {
        presetSaveSheet?.dismiss()
        presetSaveSheet = null
        Toast.makeText(this, "Saved preset \"$name\"", Toast.LENGTH_SHORT).show()
    }

    override fun onPresetLoaded(name: String) {
        presetLoadSheet?.dismiss()
        presetLoadSheet = null
        Toast.makeText(this, "Loaded preset \"$name\"", Toast.LENGTH_SHORT).show()
    }
}
