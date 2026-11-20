package com.clmix

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ValueAnimator
import android.content.Intent
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.Surface
import android.view.View
import android.view.ViewGroup
import android.text.InputFilter
import android.text.InputType
import android.view.animation.DecelerateInterpolator
import android.widget.EditText
import androidx.appcompat.app.AlertDialog
import androidx.activity.OnBackPressedCallback
import android.widget.FrameLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.core.view.isVisible
import androidx.core.view.updateLayoutParams
import androidx.core.view.updatePadding
import androidx.drawerlayout.widget.DrawerLayout
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.recyclerview.widget.LinearLayoutManager
import com.clmix.databinding.ActivityMixerControlBinding

private const val STATE_BANK = "bank"

// The longest name the rename box takes, the server's own
// MAX_CHANNEL_NAME (services/remote_server.py).
private const val MAX_STRIP_NAME_LENGTH = 32

// As MixerActivity: a panel animation short enough to read as a control
// rather than a transition.
private const val BANK_PANEL_ANIM_MS = 100L

// As MixerActivity: how long the landscape panel takes to slide out.
private const val PANEL_ANIM_MS = 180L

// Screens shorter than this (dp) get the EQ, dynamics and insert buttons
// beside each fader rather than above it - a phone on its side. A tablet
// on its side is far taller and keeps the usual stack.
private const val SIDE_CONTROLS_MAX_HEIGHT_DP = 500

/**
 * Full Mixer Control: the console's own channel faders, pans and mutes.
 *
 * The same strips as MixerActivity, riding different parameters - the
 * socket is put into mixer mode on resume (MixerClient.selectMixerControl)
 * and from then on every level/pan/mute write the adapter makes lands on
 * the channel itself rather than on one aux's sends. Where an account
 * holding the permission lands after login (see ConnectActivity), with
 * the aux screens a slide away behind the menu's AUX; the server checks
 * the permission again on every write.
 *
 * What is deliberately absent, because it belongs to mixing one send:
 * the aux picker sheet and presets.
 *
 * Each strip also carries an EQ button under its name, which opens that
 * channel's EQ full screen (EqActivity).
 */
class MixerControlActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityMixerControlBinding
    private lateinit var profileHeader: ProfileHeader
    private lateinit var adapter: ChannelAdapter
    // Whether the last bank list came with the surface layout. The
    // layered list arrives just ahead of the plain names in the same
    // message, and wins over them; a server too old to send it leaves
    // this false and the names are shown as one unlabelled layer.
    private var gotLayout = false

    private var selectedBank: String? = null
    private var smoothEnabled = false
    private val draggingChannels = mutableSetOf<Int>()
    private val dragReleasedAt = mutableMapOf<Int, Long>()
    private var bankPanelAnimator: ValueAnimator? = null
    private var panSheet: PanBottomSheet? = null
    private var inputSheet: ChannelInputBottomSheet? = null
    private var insertSheet: InsertBottomSheet? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMixerControlBinding.inflate(layoutInflater)
        setContentView(binding.root)
        enterFullScreen()

        selectedBank = savedInstanceState?.getString(STATE_BANK)

        adapter = ChannelAdapter(
            onLevelChanged = { channel, db -> MixerClient.setLevel(channel, db) },
            onDragStart = { channel -> draggingChannels.add(channel) },
            onDragEnd = { channel ->
                draggingChannels.remove(channel)
                dragReleasedAt[channel] = System.currentTimeMillis()
            },
            onPanButtonClicked = { channel -> showPanSheet(channel) },
            onMuteToggled = { channel, muted ->
                MixerClient.setMute(channel, muted)
            },
            onChannelNumberClicked = { channel -> showChannelInputSheet(channel) },
            onEqClicked = { channel -> openEq(channel) },
            onDynamicsClicked = { channel, comp -> openDynamics(channel, comp) },
            onInsertClicked = { channel, slot -> showInsertSheet(channel, slot) },
            onHardMuteToggled = { channel, muted -> MixerClient.setHardMute(channel, muted) }
        )

        // Both are the console's own controls, and Full Mixer Control is
        // the single permission covering them - unlike the aux screen,
        // where Mute is gated separately and Pan depends on the bus being
        // stereo. A mono channel still reports no pan value, and the
        // strip drops the control for that channel on its own.
        adapter.muteSupported = true
        adapter.panSupported = true
        applySideControls()

        // The console's own numbering, above each name - see
        // ChannelAdapter.showChannelNumber.
        adapter.showChannelNumber = true

        // The meter sits beside the name here, small, leaving the fader
        // row to the fader - see ChannelAdapter.meterBesideName.
        adapter.meterBesideName = true

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

        binding.bankPanel.onPick = { bank ->
            setBanksExpanded(false)
            if (openPanel == Panel.BANK) closeLandscapePanel()

            if (bank.id != selectedBank) {
                selectedBank = bank.id
                binding.bankPanel.select(bank.id)
                MixerClient.selectBank(bank.id)
            }
        }

        // Into the top bar on a tablet, each layer's label at the start of
        // its row so the bar does not grow a line per layer.
        if (isLargeScreen()) {
            binding.bankPanel.inline = true
            moveBanksIntoTopBar(binding.topBar, binding.topBarSpacer, binding.bankPanel)
        }

        binding.bankToggle.setOnClickListener {
            setBanksExpanded(binding.bankPanel.visibility != View.VISIBLE)
        }
        applyLargeScreen()

        binding.menuButton.setOnClickListener {
            binding.drawerLayout.openDrawer(GravityCompat.START)
        }

        // The landscape sidebar - see applyOrientation.
        binding.sidebarMenuButton.setOnClickListener { toggleLandscapePanel(Panel.MENU) }
        binding.sidebarBankButton.setOnClickListener { toggleLandscapePanel(Panel.BANK) }
        binding.sidebarSmoothButton.setOnClickListener {
            smoothEnabled = !smoothEnabled
            adapter.smoothEnabled = smoothEnabled
            updateSmoothButtonAppearance()
        }
        binding.landscapeScrim.setOnClickListener { closeLandscapePanel() }
        binding.root.addOnLayoutChangeListener(sidebarSideOnLayout)
        applyOrientation()


        // AUX slides over to the aux list, to mix one send. It needs the
        // list first, so it asks for it and opens once it arrives (see
        // onAuxes). This screen stays underneath: Channels, or Back from
        // the list, slides back to it.
        bindModeButtons(binding.modeButtons, MixScreen.CHANNELS) {
            closeMenu()
            dismissPanSheet()
            dismissInputSheet()
            awaitingAuxes = true
            MixerClient.requestAuxes()
        }

        // The first screen after login for an account holding Mixer
        // Control, with the login screen still underneath and still
        // connected - so backing out of it logs out, as backing out of
        // the aux list does for everyone else.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = logout()
        })

        binding.logoutButton.setOnClickListener { logout() }

        // Chat, from the menu or - with the phone on its side - the sidebar.
        binding.chatButton.setOnClickListener { openChat() }
        binding.sidebarChatButton.setOnClickListener { openChat() }

        profileHeader = ProfileHeader(binding.profileHeader) {
            closeMenu()
            dismissPanSheet()
            dismissInputSheet()
            startActivity(Intent(this, ProfileActivity::class.java))
        }

        binding.smoothButton.setOnClickListener {
            smoothEnabled = !smoothEnabled
            adapter.smoothEnabled = smoothEnabled
            updateSmoothButtonAppearance()
        }
        updateSmoothButtonAppearance()
    }

    // Set while waiting for the aux list AUX asked for, so the reply is
    // only acted on when it was asked for here - the server pushes an aux
    // list for other reasons too.
    private var awaitingAuxes = false

    override fun onAuxes(auxes: List<AuxBus>) {
        if (!awaitingAuxes) return

        awaitingAuxes = false
        startActivity(
            Intent(this, AuxListActivity::class.java).putExtra("auxes", ArrayList(auxes))
        )
        slideToAux()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(STATE_BANK, selectedBank)
    }

    override fun onResume() {
        super.onResume()
        enterFullScreen()

        if (!MixerClient.isConnected) {
            returnToLogin()
            return
        }

        MixerClient.claimListener(this)
        profileHeader.start()

        // Re-asserted on every resume rather than once in onCreate: the
        // socket is shared with the other screens, and coming back here
        // from one of them (or after a rebuild) must put it back into
        // mixer mode before any fader on screen is touched.
        reselect()
    }

    /**
     * Puts the socket in mixer mode and asks for the banks - on resume,
     * and again once a desktop resync is over (see ServerSync), which
     * drops whatever this phone had selected.
     */
    fun reselect() {
        MixerClient.selectMixerControl()
        MixerClient.requestBanks()
    }

    override fun onPause() {
        super.onPause()
        profileHeader.stop()
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
        // A multi, DCA or bus has no input stage - its heading renames it.
        if (!channel.isInput) {
            showStripRenameDialog(channel)
            return
        }

        val sheet = ChannelInputBottomSheet(
            context = this,
            initial = channel,
            gainRange = MixerClient.gainRange,
            trimRange = MixerClient.trimRange,
            onGainChanged = { ch, gain -> MixerClient.setGain(ch, gain) },
            onTrimChanged = { ch, trim -> MixerClient.setTrim(ch, trim) },
            onDelayChanged = { ch, ms -> MixerClient.setDelay(ch, ms) },
            onDelayOnChanged = { ch, on -> MixerClient.setDelayOn(ch, on) },
            onFineDelayChanged = { ch, samples -> MixerClient.setFineDelay(ch, samples) },
            onPhantomChanged = { ch, on -> MixerClient.setPhantom(ch, on) },
            onAltGainChanged = { ch, gain -> MixerClient.setAltGain(ch, gain) },
            onAltPhantomChanged = { ch, on -> MixerClient.setAltPhantom(ch, on) },
            onAltInChanged = { ch, altIn -> MixerClient.setAltIn(ch, altIn) },
            onPhaseChanged = { ch, state -> MixerClient.setPhase(ch, state) },
            onNameChanged = { ch, name -> MixerClient.setName(ch, name) }
        )

        sheet.setOnDismissListener {
            if (inputSheet === sheet) inputSheet = null
        }

        inputSheet = sheet
        sheet.show()
    }

    /**
     * Renames a multi, DCA or bus on the desk itself - its name is the
     * console's, as an input's is, so every surface and the next snapshot
     * store see it. No optimistic update: the desk echoes the new name
     * and the next push carries it.
     */
    private fun showStripRenameDialog(channel: ChannelState) {
        val field = EditText(this).apply {
            setText(channel.name)
            setSelection(text.length)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS
            filters = arrayOf(InputFilter.LengthFilter(MAX_STRIP_NAME_LENGTH))
        }

        // AlertDialog gives a custom view no padding of its own.
        val padding = (resources.displayMetrics.density * 20).toInt()
        val frame = FrameLayout(this).apply {
            setPadding(padding, padding / 2, padding, 0)
            addView(field)
        }

        AlertDialog.Builder(this)
            .setTitle("Rename ${channel.label ?: channel.channel}")
            .setMessage("Renames it on the mixer, for every surface.")
            .setView(frame)
            .setPositiveButton("Save") { _, _ ->
                val name = field.text.toString().trim()
                if (name.isNotEmpty()) MixerClient.setName(channel.channel, name)
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // The EQ is its own landscape screen rather than a sheet: a frequency
    // graph needs the width. The sheets are closed first so coming back
    // lands on the strips, not on a sheet for some other channel.
    private fun openEq(channel: ChannelState) {
        dismissPanSheet()
        dismissInputSheet()
        EqActivity.start(this, channel)
    }

    private fun openDynamics(channel: ChannelState, comp: Boolean) {
        dismissPanSheet()
        dismissInputSheet()
        DynamicsActivity.start(this, channel, comp)
    }

    private fun dismissInputSheet() {
        inputSheet?.dismiss()
        inputSheet = null
        insertSheet?.dismiss()
        insertSheet = null
    }

    private fun openChat() {
        closeMenu()
        dismissPanSheet()
        dismissInputSheet()
        startActivity(Intent(this, ChatActivity::class.java))
    }

    private fun showInsertSheet(channel: ChannelState, slot: String) {
        // A bus's insert is in or out and nothing more - no head amp on
        // the return - so its button flips it straight away instead.
        if (!channel.isInput) {
            val current = (if (slot == "A") channel.insertA else channel.insertB)?.inOn == true
            MixerClient.setInsert(channel.channel, slot, "in", !current)
            return
        }

        val sheet = InsertBottomSheet(
            context = this,
            initial = channel,
            slot = slot,
            onChanged = { ch, slot, param, value -> MixerClient.setInsert(ch, slot, param, value) }
        )

        sheet.setOnDismissListener {
            if (insertSheet === sheet) insertSheet = null
        }

        insertSheet = sheet
        sheet.show()
    }

    private fun updateSmoothButtonAppearance() {
        val backgroundRes = if (smoothEnabled) R.color.secondary else R.color.mute_inactive
        val textRes = if (smoothEnabled) R.color.on_secondary else R.color.on_mute_inactive

        binding.smoothButton.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(this, backgroundRes)
        )
        binding.smoothButton.setTextColor(ContextCompat.getColor(this, textRes))

        // The sidebar's Fine is the same control, so it wears the same look.
        binding.sidebarSmoothButton.backgroundTintList = ColorStateList.valueOf(
            ContextCompat.getColor(this, backgroundRes)
        )
        binding.sidebarSmoothButton.setTextColor(ContextCompat.getColor(this, textRes))
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applyLargeScreen()
        applySideControls()
        applyOrientation()
    }

    // ---- Landscape sidebar: as MixerActivity's, with the menu and the
    // ---- banks (no aux list or SOS here).

    private enum class Panel { MENU, BANK }

    private var openPanel: Panel? = null

    // Which edge the sidebar is currently built for. Null until the
    // first landscape layout.
    private var appliedCameraOnRight: Boolean? = null

    /** Every layout re-checks which edge the camera is on, which is what
     *  catches a flip from one landscape to the other (see MixerActivity). */
    private val sidebarSideOnLayout =
        View.OnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> applySidebarSide() }

    /** Whether this screen folds its bars into the landscape side strip:
     *  a phone on its side. A tablet keeps its top bar. */
    private fun usesSidebar(): Boolean =
        resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE &&
            !isLargeScreen()

    /**
     * Landscape folds the top bar and the snapshot line away and moves
     * what the bar held - menu, banks, Fine - into a strip down the edge
     * the selfie camera is on, as on the aux screen. Portrait unchanged.
     */
    private fun applyOrientation() {
        val landscape = usesSidebar()

        // Closed before anything moves: a drawer cannot be taken out
        // from under itself while it is open.
        closeMenu()
        closeLandscapePanel(animate = false)
        hostMenu(inPanel = landscape)
        hostBanks(inPanel = landscape)
        binding.landscapeSidebar.isVisible = landscape

        appliedCameraOnRight = null
        if (landscape) {
            applySidebarSide()
        } else {
            binding.mixerColumn.updatePadding(left = 0, right = 0)
        }

        // The strips get the whole height, which is the point of landscape.
        binding.topBar.isVisible = !landscape
        binding.snapshotBar.root.isVisible = !landscape
    }

    /** Puts the sidebar, the panel and the chevron on the camera's edge.
     *  Driven off every layout - see MixerActivity.applySidebarSide for
     *  why a rotation callback is not enough. */
    private fun applySidebarSide() {
        if (!usesSidebar()) return

        val onRight = cameraOnRight()
        if (appliedCameraOnRight == onRight) return
        appliedCameraOnRight = onRight

        closeLandscapePanel(animate = false)

        val edge = if (onRight) Gravity.END else Gravity.START
        val sidebar = resources.getDimensionPixelSize(R.dimen.landscape_sidebar_width)

        binding.landscapeSidebar.updateLayoutParams<FrameLayout.LayoutParams> {
            gravity = edge
        }
        // Inset by the sidebar so the panel comes out beside it rather
        // than under it - the sidebar is what closes it again.
        binding.landscapePanel.updateLayoutParams<FrameLayout.LayoutParams> {
            gravity = edge
            marginStart = if (onRight) 0 else sidebar
            marginEnd = if (onRight) sidebar else 0
        }

        // The chevron points into the screen, the way the panel travels.
        binding.sidebarBankArrow.scaleX = if (onRight) 1f else -1f

        // The strips stop at the sidebar rather than running under it.
        binding.mixerColumn.updatePadding(
            left = if (onRight) 0 else sidebar,
            right = if (onRight) sidebar else 0
        )
    }

    private fun currentRotation(): Int = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
        display?.rotation ?: Surface.ROTATION_0
    } else {
        @Suppress("DEPRECATION")
        windowManager.defaultDisplay.rotation
    }

    private fun cameraOnRight(): Boolean {
        val cutout = window.decorView.rootWindowInsets?.displayCutout

        if (cutout != null) {
            if (cutout.safeInsetRight > 0) return true
            if (cutout.safeInsetLeft > 0) return false
        }

        return currentRotation() != Surface.ROTATION_90
    }

    /** Puts the menu away wherever it is: the drawer, or the panel. */
    private fun closeMenu() {
        binding.drawerLayout.closeMenuDrawer()
        closeLandscapePanel()
    }

    /**
     * Moves the drawer's content between the drawer and the sidebar's
     * panel - see MixerActivity.hostMenu. A drawer slides out from the
     * screen's own edge, over the sidebar that opened it.
     */
    private fun hostMenu(inPanel: Boolean) {
        // Docked for good on a tablet.
        if (isLargeScreen()) return

        val menu = binding.menuContent
        val host = binding.landscapePanelMenuHost

        if (inPanel) {
            if (menu.parent === host) return

            binding.drawerLayout.removeView(menu)
            host.addView(
                menu,
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT
                )
            )
            // DrawerLayout leaves a closed drawer INVISIBLE - see
            // MixerActivity.hostMenu.
            menu.isVisible = true
            return
        }

        if (menu.parent === binding.drawerLayout) return

        host.removeView(menu)
        binding.drawerLayout.addView(
            menu,
            DrawerLayout.LayoutParams(
                resources.getDimensionPixelSize(R.dimen.side_menu_width),
                DrawerLayout.LayoutParams.MATCH_PARENT,
                Gravity.START
            )
        )
    }

    // Whether the bank panel is in the landscape panel rather than
    // floating over the strips.
    private var banksInPanel = false

    /**
     * Moves the bank panel - every layer of the surface - between its
     * spot floating over the strips and the sidebar's panel. One panel
     * moved rather than two kept in step, as with the menu.
     */
    private fun hostBanks(inPanel: Boolean) {
        // On a tablet it lives in the top bar instead.
        if (isLargeScreen() || banksInPanel == inPanel) return

        val panel = binding.bankPanel
        bankPanelAnimator?.cancel()
        bankPanelAnimator = null
        (panel.parent as? ViewGroup)?.removeView(panel)

        if (inPanel) {
            binding.landscapePanelBankHost.addView(
                panel,
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT
                )
            )
            panel.visibility = View.VISIBLE
        } else {
            val frame = binding.channelRecycler.parent as ViewGroup
            frame.addView(
                panel,
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    Gravity.TOP
                )
            )
            panel.visibility = View.GONE
        }

        banksInPanel = inPanel
    }

    private fun toggleLandscapePanel(panel: Panel) {
        if (openPanel == panel) {
            closeLandscapePanel()
            return
        }

        openPanel = panel
        binding.landscapePanelTitle.text = if (panel == Panel.MENU) "MENU" else "BANK"
        binding.landscapePanelMenuHost.isVisible = panel == Panel.MENU
        binding.landscapePanelBankScroll.isVisible = panel == Panel.BANK

        binding.landscapeScrim.isVisible = true
        binding.landscapePanel.isVisible = true

        // Off its own edge and back, so it comes out from under the sidebar.
        binding.landscapePanel.translationX = hiddenPanelOffset()
        binding.landscapePanel.animate()
            .translationX(0f)
            .setDuration(PANEL_ANIM_MS)
            .start()
    }

    /** Far enough to clear its own width and the sidebar it is inset by. */
    private fun hiddenPanelOffset(): Float {
        val distance = resources.getDimensionPixelSize(R.dimen.landscape_panel_width) +
            resources.getDimensionPixelSize(R.dimen.landscape_sidebar_width)

        return if (cameraOnRight()) distance.toFloat() else -distance.toFloat()
    }

    private fun closeLandscapePanel(animate: Boolean = true) {
        if (openPanel == null) {
            binding.landscapePanel.isVisible = false
            binding.landscapeScrim.isVisible = false
            return
        }

        openPanel = null
        binding.landscapeScrim.isVisible = false

        val hidden = hiddenPanelOffset()

        if (!animate) {
            binding.landscapePanel.animate().cancel()
            binding.landscapePanel.translationX = hidden
            binding.landscapePanel.isVisible = false
            return
        }

        binding.landscapePanel.animate()
            .translationX(hidden)
            .setDuration(PANEL_ANIM_MS)
            .withEndAction { binding.landscapePanel.isVisible = false }
            .start()
    }

    /**
     * On a short screen - a phone turned on its side - the EQ, dynamics
     * and insert buttons go beside each fader instead of above it, which
     * would leave the fader no length at all. See ChannelAdapter.sideControls.
     */
    private fun applySideControls() {
        adapter.sideControls = resources.configuration.screenHeightDp < SIDE_CONTROLS_MAX_HEIGHT_DP
    }

    /** As MixerActivity: a tablet keeps its banks out, in the top bar,
     *  with no arrow. */
    private fun applyLargeScreen() {
        val large = isLargeScreen()
        binding.bankToggle.isVisible = !large
        if (large) setBanksExpanded(true)

        // As MixerActivity: docked on a tablet on its side.
        val docked = wantsDockedMenu()
        setSideMenuDocked(binding.drawerLayout, binding.menuContent, binding.mixerFrame, docked)
        binding.menuButton.isVisible = !docked
    }

    /** Identical to MixerActivity's panel, minus the aux sheet it also
     *  closed - and, as there, always open on a tablet. */
    private fun setBanksExpanded(requested: Boolean) {
        // In landscape the banks sit in the sidebar's panel, which opens
        // and closes itself - see hostBanks.
        if (banksInPanel) return

        val expanded = requested || isLargeScreen()
        val panel = binding.bankPanel
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
        inputSheet?.updateMeter(sequence, meters)
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

    override fun onBankLayers(layers: List<BankLayer>) {
        gotLayout = true
        showBanks(layers)
    }

    override fun onBanks(banks: List<String>) {
        // Already shown, from the layout that came in the same message.
        if (gotLayout) {
            gotLayout = false
            return
        }

        // An older server: the names alone, as one unlabelled layer.
        showBanks(listOf(BankLayer(0, banks.map { LayoutBank(it, it, 1) })))
    }

    private fun showBanks(layers: List<BankLayer>) {
        val usable = layers.flatMap { it.banks }.filter { it.inputs > 0 }

        if (usable.isEmpty()) {
            // A console reporting no banks with strips on: fall back to
            // every channel, exactly as the aux screen does.
            if (selectedBank != null) {
                selectedBank = null
                MixerClient.selectBank(null)
            }

            binding.bankPanel.show(layers, null)
            setBanksExpanded(false)
            return
        }

        // Whatever was showing before, if it is still there and still has
        // strips on it; else the surface's first bank that does.
        val bank = usable.firstOrNull { it.id == selectedBank } ?: usable.first()

        if (bank.id != selectedBank) {
            selectedBank = bank.id
            MixerClient.selectBank(bank.id)
        }

        binding.bankPanel.show(layers, bank.id)
    }

    override fun onLevels(aux: Int, channels: List<ChannelState>) {
        binding.snapshotBar.root.showSnapshot(MixerClient.liveSnapshot)
        // Anything still arriving for a bus belongs to the aux screen -
        // most likely a frame already in flight when the socket switched
        // modes, which would otherwise paint send levels onto these
        // console faders for one frame.
        if (aux != MixerClient.MIXER_AUX) return

        // Each strip where the desk has it, empty faders left empty.
        adapter.updateChannels(
            withGaps(channels, MixerClient.bankSlots), draggingChannels, dragReleasedAt
        )

        panSheet?.let { sheet ->
            channels.firstOrNull { it.channel == sheet.channel }?.pan?.let(sheet::updateIfIdle)
        }

        inputSheet?.let { sheet ->
            channels.firstOrNull { it.channel == sheet.channel }?.let(sheet::update)
        }

        insertSheet?.let { sheet ->
            channels.firstOrNull { it.channel == sheet.channel }?.let(sheet::update)
        }
    }
}
