package com.clmix

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Intent
import android.content.res.Configuration
import android.content.res.ColorStateList
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.core.view.updateLayoutParams
import androidx.core.view.updatePadding
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.clmix.databinding.ActivityMixerBinding

private const val STATE_AUX_INDEX = "auxIndex"
private const val STATE_AUX_NAME = "auxName"
private const val STATE_BANK = "bank"

// Bank buttons per row in the pull-down panel. Four keeps a typical
// name readable at phone width while still showing a couple of dozen
// banks without scrolling.
private const val BANK_COLUMNS = 4

// Bank panel open/close. Deliberately well under Android's own 200ms
// "short" duration: this is a control being operated mid-show, not a
// screen transition, so it only has to take the hard edge off the
// strips jumping - any longer reads as waiting for the panel.
private const val BANK_PANEL_ANIM_MS = 100L

// How much of the screen the expanded aux sheet covers. Enough to show
// a handful of mixes at once, short of swallowing the whole display -
// the strips behind it stay partly visible, which is the point of it
// floating over them rather than replacing them.
private const val AUX_SHEET_SCREEN_FRACTION = 0.55f

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
    // Kept so switchAux() can look up the incoming bus's width - the
    // drawer hands over an AuxBus, but a rebuild restores only auxIndex.
    private var auxBuses: List<AuxBus> = emptyList()
    private var auxAdapter: AuxAdapter? = null
    private lateinit var bankAdapter: BankAdapter
    private var bankPanelAnimator: ValueAnimator? = null
    private var auxSheet: BottomSheetBehavior<android.widget.LinearLayout>? = null
    // Guards the scroll-to-selection so it runs once per opening rather
    // than on every frame of the drag, which would fight the finger.
    private var auxListScrolled = false

    // Landscape only. The bars fold away by default there, since that is
    // the orientation where they cost the most - portrait always shows
    // them and never shows the toggle.
    private var chromeVisible = true
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
        auxBuses = auxes

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

                // The strips themselves were measured against the old
                // height and RecyclerView will happily reuse them at that
                // size - which pushes whatever is at the bottom of a
                // strip (the Mute button) past the recycler's edge. A
                // rebind alone is not enough; the item views have to be
                // asked to measure again. Posted because this fires
                // during a layout pass, where a synchronous
                // requestLayout() is swallowed - the same reason
                // syncFaderWidth defers its own.
                binding.channelRecycler.post {
                    for (i in 0 until binding.channelRecycler.childCount) {
                        binding.channelRecycler.getChildAt(i).requestLayout()
                    }
                }
            }
        }

        bankAdapter = BankAdapter { bank ->
            // Fold away on pick: the panel exists to make the choice, and
            // leaving it open would keep a third of the faders pushed off
            // screen after the choice is made.
            setBanksExpanded(false)

            if (bank != selectedBank) {
                selectedBank = bank
                bankAdapter.selectedBank = bank
                MixerClient.selectBank(bank)
            }
        }

        // A grid rather than a row: a console can report a couple of dozen
        // banks, and a single scrolling line would put most of them off
        // the edge - the point of the panel is seeing them all at once.
        binding.bankRecycler.layoutManager = GridLayoutManager(this, BANK_COLUMNS)
        binding.bankRecycler.adapter = bankAdapter

        binding.bankToggle.setOnClickListener {
            setBanksExpanded(binding.bankRecycler.visibility != View.VISIBLE)
        }

        binding.menuButton.setOnClickListener {
            binding.drawerLayout.openDrawer(GravityCompat.START)
        }

        binding.auxSheetRecycler.layoutManager = LinearLayoutManager(this)
        auxAdapter = AuxAdapter(auxes) { aux -> switchAux(aux) }
        binding.auxSheetRecycler.adapter = auxAdapter

        // An explicit height rather than wrap_content. Left to wrap, the
        // sheet measured its list at a fraction of one row and ended up
        // barely taller than its own handle - so "collapsed" still showed
        // part of the list and covered the Mute buttons. A fixed fraction
        // of the screen also gives the list a bound to scroll within when
        // a console has more auxes than fit.
        binding.auxSheet.layoutParams.height =
            (resources.displayMetrics.heightPixels * AUX_SHEET_SCREEN_FRACTION).toInt()

        auxSheet = BottomSheetBehavior.from(binding.auxSheet).apply {
            // Never dismissable: collapsed, this sheet is the label that
            // names the live mix, so there is no state where it should
            // not be on screen.
            isHideable = false

            // Set in code as well as XML, and the state forced once the
            // sheet has actually been laid out - asking for a state
            // before that happens is silently dropped, which left the
            // sheet sitting at its expanded height over the strips.
            // BottomSheetBehavior adds the bottom gesture inset to the
            // peek height by default, to keep a collapsed sheet clear of
            // the navigation pill. Here that made the collapsed sheet
            // taller than the space the strips reserve for it, so it sat
            // over the Mute buttons. This screen already draws
            // edge-to-edge with the bars hidden, so the peek is exactly
            // what is asked for and the reservation matches it.
            isGestureInsetBottomIgnored = true
            peekHeight = resources.getDimensionPixelSize(R.dimen.aux_sheet_peek)
            binding.auxSheet.post { state = BottomSheetBehavior.STATE_COLLAPSED }

            addBottomSheetCallback(object : BottomSheetBehavior.BottomSheetCallback() {
                override fun onStateChanged(sheet: View, newState: Int) {}

                override fun onSlide(sheet: View, offset: Float) {
                    // The moment it starts to move, so the list is
                    // already in the right place by the time it is
                    // readable rather than jumping once it settles.
                    if (offset > 0f && !auxListScrolled) {
                        auxListScrolled = true
                        scrollAuxListToSelection()
                    } else if (offset == 0f) {
                        auxListScrolled = false
                    }
                }
            })

            addBottomSheetCallback(object : BottomSheetBehavior.BottomSheetCallback() {
                override fun onStateChanged(sheet: View, newState: Int) {
                    if (newState == BottomSheetBehavior.STATE_EXPANDED ||
                        newState == BottomSheetBehavior.STATE_COLLAPSED
                    ) {
                        setAuxArrows(newState == BottomSheetBehavior.STATE_EXPANDED)
                    }
                }

                override fun onSlide(sheet: View, offset: Float) {
                    // Turn with the drag rather than snapping at the end,
                    // so the arrows track the finger while it moves.
                    setAuxArrowRotation(offset * 180f)
                }
            })
        }

        // Dragging the handle is BottomSheetBehavior's own doing; this
        // only adds the tap, which it does not provide.
        binding.auxHandle.setOnClickListener {
            val opening = auxSheet?.state != BottomSheetBehavior.STATE_EXPANDED

            if (opening) {
                auxListScrolled = true
                scrollAuxListToSelection()
            }

            auxSheet?.state = if (opening) {
                BottomSheetBehavior.STATE_EXPANDED
            } else {
                BottomSheetBehavior.STATE_COLLAPSED
            }
        }

        // Only accounts with Preset Access (Setup > Accounts on desktop)
        // get this button at all - the server enforces the same check
        // independently, but there's no point showing an action that
        // would just come back as an error.
        binding.presetsButton.visibility = if (MixerClient.presetsAllowed) View.VISIBLE else View.GONE

        adapter.muteSupported = MixerClient.muteAllowed
        applyAuxWidth()
        showCurrentAux()
        applyOrientation()
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

        binding.chromeToggle.setOnClickListener {
            chromeVisible = !chromeVisible
            applyChrome()
        }

        binding.logoutButton.setOnClickListener { logout() }

        binding.smoothButton.setOnClickListener {
            smoothEnabled = !smoothEnabled
            adapter.smoothEnabled = smoothEnabled
            updateSmoothButtonAppearance()
        }
        updateSmoothButtonAppearance()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applyOrientation()
    }

    /**
     * Folds the bars away in landscape and offers the toggle that brings
     * them back; portrait keeps them and hides the toggle.
     */
    private fun applyOrientation() {
        val landscape =
            resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE

        binding.chromeToggle.visibility = if (landscape) View.VISIBLE else View.GONE

        // Turning the phone starts from hidden - the point of landscape
        // is the extra fader travel, so that is what it opens on.
        chromeVisible = !landscape
        applyChrome()
    }

    private fun applyChrome() {
        binding.topBar.visibility = if (chromeVisible) View.VISIBLE else View.GONE
        binding.auxSheet.visibility = if (chromeVisible) View.VISIBLE else View.GONE

        // The column reserves a strip for the collapsed aux sheet; with
        // the sheet gone that reservation is just wasted fader travel.
        binding.mixerColumn.updatePadding(
            bottom = if (chromeVisible) {
                resources.getDimensionPixelSize(R.dimen.aux_sheet_peek)
            } else {
                0
            }
        )

        if (!chromeVisible) {
            // Neither panel should be left open behind a hidden bar,
            // where nothing could close it.
            setBanksExpanded(false)
            auxSheet?.state = BottomSheetBehavior.STATE_COLLAPSED
        }

        // Lifted clear of the aux bar when that is showing, or it would
        // sit half-buried behind the very bar it is there to dismiss.
        val margin = resources.getDimensionPixelSize(R.dimen.chrome_toggle_margin)
        binding.chromeToggle.updateLayoutParams<ViewGroup.MarginLayoutParams> {
            bottomMargin = if (chromeVisible) {
                margin + resources.getDimensionPixelSize(R.dimen.aux_sheet_peek)
            } else {
                margin
            }
        }

        // The icon says what pressing it will do, not what is showing.
        binding.chromeToggle.setImageResource(
            if (chromeVisible) R.drawable.ic_eye_off else R.drawable.ic_eye
        )
        binding.chromeToggle.contentDescription =
            if (chromeVisible) "Hide controls" else "Show controls"
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt(STATE_AUX_INDEX, auxIndex)
        outState.putString(STATE_AUX_NAME, title?.toString())
        outState.putString(STATE_BANK, selectedBank)
    }

    override fun onMeters(sequence: Long, meters: Map<Int, MeterLevels>) {
        adapter.updateMeters(sequence, meters, binding.channelRecycler)
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

        // The connection can end while this screen is in the background,
        // where there is no listener to hear it - the phone leaving the
        // building is the usual way. Coming back to a grid of faders that
        // look live but are attached to nothing is worse than being asked
        // to log in again, so check rather than assume.
        if (!MixerClient.isConnected) {
            returnToLogin()
            return
        }

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
        auxSheet?.state = BottomSheetBehavior.STATE_COLLAPSED

        if (aux.index == auxIndex) return

        // Pan/presets are per-aux-send, so sheets left open from the
        // previous aux would otherwise keep acting against the new one.
        dismissPanSheet()
        dismissPresetSheets()

        auxIndex = aux.index
        title = aux.name
        applyAuxWidth()
        showCurrentAux()
        MixerClient.selectAux(auxIndex)
    }

    // A mono aux has no pan axis, so the Pan button comes off the strip
    // entirely - the same thing the desktop does with its pan slider.
    // Unknown aux (an older server, or a rebuild before the list
    // arrives) is treated as stereo, which is how it always behaved.
    // Everything that names the aux currently being mixed: the bar along
    // the bottom of the strips, and the highlight in the drawer's list.
    // Called from the same places as applyAuxWidth so the two can never
    // describe different auxes.
    private fun setBanksExpanded(expanded: Boolean) {
        val panel = binding.bankRecycler
        val alreadyThere = (panel.visibility == View.VISIBLE) == expanded

        // Nothing to do, and nothing to animate - this is also what makes
        // the collapse calls from onBanks and from picking a bank free
        // when the panel was never open.
        if (alreadyThere && bankPanelAnimator == null) return

        bankPanelAnimator?.cancel()

        // Points the way it will move, not at what it is.
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
            // Decelerating suits a panel being pulled down and released:
            // it arrives rather than stopping dead.
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

    /**
     * Hands the panel back to the layout once the animation is done: a
     * pinned pixel height would stop it growing if the bank list changed
     * under it.
     */
    private fun finishBankPanel(panel: View, expanded: Boolean) {
        panel.layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
        panel.visibility = if (expanded) View.VISIBLE else View.GONE
        panel.requestLayout()
    }

    /** How tall the panel wants to be, without showing it at that size first. */
    private fun measureBankPanelHeight(panel: View): Int {
        val available = (panel.parent as? View)?.width ?: return 0

        panel.measure(
            View.MeasureSpec.makeMeasureSpec(available, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        )

        return panel.measuredHeight
    }

    /**
     * Brings the live mix into view before the sheet arrives at it.
     *
     * With a handful of auxes everything fits and this does nothing. On
     * a console with thirty, opening the sheet would otherwise land on
     * the top of the list with the mix you are actually on somewhere
     * below the fold - so the first thing you would do every time is
     * scroll to find it.
     */
    private fun scrollAuxListToSelection() {
        val manager = binding.auxSheetRecycler.layoutManager as? LinearLayoutManager ?: return
        val index = auxBuses.indexOfFirst { it.index == auxIndex }

        if (index < 0) return

        // One row above the selection where there is room, so it arrives
        // with some context rather than jammed against the top edge.
        manager.scrollToPositionWithOffset(maxOf(0, index - 1), 0)
    }

    private fun setAuxArrows(expanded: Boolean) {
        setAuxArrowRotation(if (expanded) 180f else 0f)
        binding.auxHandle.contentDescription =
            if (expanded) "Hide aux list" else "Show aux list"
    }

    private fun setAuxArrowRotation(degrees: Float) {
        binding.auxArrowStart.rotation = degrees
        binding.auxArrowEnd.rotation = degrees
    }

    private fun showCurrentAux() {
        binding.currentAuxLabel.text = title?.toString().orEmpty()
        auxAdapter?.selectedAux = auxIndex
    }

    private fun applyAuxWidth() {
        val aux = auxBuses.firstOrNull { it.index == auxIndex }
        adapter.panSupported = aux?.stereo ?: true

        if (aux?.stereo == false) {
            dismissPanSheet()
        }
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
        // No synthetic "All" entry: every channel at once is a whole
        // console's worth of strips, faders and meters for a phone that
        // can show a dozen - the same reason the desktop dropped its own
        // All button. The console's banks are the only choices.
        bankAdapter.submit(banks)

        if (banks.isEmpty()) {
            // A console that reports no banks at all: fall back to every
            // channel rather than leaving the operator with an empty
            // screen. A fallback, not a choice - there is still nothing
            // in the spinner to pick.
            if (selectedBank != null) {
                selectedBank = null
                bankAdapter.selectedBank = null
                MixerClient.selectBank(null)
            }

            setBanksExpanded(false)
            return
        }

        // Whatever was showing before a rebuild, else the console's own
        // first bank. Unlike the spinner this replaced, nothing selects
        // itself here - so the opening bank has to be asked for
        // explicitly rather than falling out of an adapter callback.
        val bank = selectedBank.takeIf { it in banks } ?: banks.first()

        if (bank != selectedBank) {
            selectedBank = bank
            MixerClient.selectBank(bank)
        }

        bankAdapter.selectedBank = bank
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
