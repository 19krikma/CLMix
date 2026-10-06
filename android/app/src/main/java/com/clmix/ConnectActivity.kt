package com.clmix

import android.Manifest
import android.animation.ArgbEvaluator
import android.animation.ValueAnimator
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.text.Editable
import android.text.TextWatcher
import android.graphics.Color
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.Bundle
import android.view.KeyEvent
import android.view.inputmethod.EditorInfo
import android.transition.AutoTransition
import android.transition.TransitionManager
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.animation.doOnEnd
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.doOnNextLayout
import com.clmix.databinding.ActivityConnectBinding
import com.google.android.material.button.MaterialButton
import kotlin.math.max

class ConnectActivity : AppCompatActivity(), MixerClientListener, MdnsDiscoveryListener {
    private lateinit var binding: ActivityConnectBinding
    private lateinit var prefs: SharedPreferences
    private lateinit var mdnsDiscovery: MdnsDiscovery

    // Keyed by mDNS service name so a re-announcement updates the existing
    // row in place instead of piling up duplicates.
    private val discoveredRows = mutableMapOf<String, MaterialButton>()

    // The row for whichever discovered server was last tapped - only this
    // one is filled blue at a time, everything else stays outlined.
    private var selectedRow: MaterialButton? = null

    // Stashed from the form (or a saved session) at connect time so
    // onConnected() knows how to log in once the socket is open, without
    // persisting the password itself to disk. Exactly one of
    // pendingPassword/pendingToken is used per attempt.
    private var pendingUsername: String = ""
    private var pendingPassword: String = ""
    private var pendingToken: String? = null

    // True while the two credential fields are shown as rejected - the
    // one thing typing into them clears.
    private var credentialsRejected = false

    // Whether what's on the message line is a failure rather than
    // progress. Both onLoginResult and onError report a failure and then
    // close the socket, and that close comes back through onDisconnected
    // a moment later - which must not wipe the message explaining what
    // just went wrong.
    private var showingError = false

    // The button's normal look, captured before anything recolors it -
    // the day/night resources behind these are resolved at inflation, and
    // a theme change recreates this activity, so they stay correct.
    private var buttonBackground: ColorStateList? = null

    // Runs the blue <-> red tint change. Held so a second result landing
    // mid-fade cancels the first rather than fighting it.
    private var colorAnimator: ValueAnimator? = null

    // A failure shown on the button itself holds for RESULT_HOLD_MS and
    // then puts the button back. Tracked so the callbacks that fire in
    // the meantime - notably onDisconnected, which arrives right after
    // the socket the failure closed - don't reset it early.
    private val resultHandler = Handler(Looper.getMainLooper())
    private var showingButtonResult = false

    // A scan window: open on arrival and again on every tap of the
    // refresh control, closed SCAN_WINDOW_MS later. It is what the
    // spinner reports and what decides whether Manual is ever offered -
    // discovery itself keeps browsing underneath either way, so a server
    // that announces itself late still turns up in the list.
    private val scanHandler = Handler(Looper.getMainLooper())

    // Set once a whole scan has gone by with nothing found. It is not
    // the same thing as Manual being on screen - see
    // updateManualVisibility - but it never goes back to false: having
    // once had to fall back to typing an address is a fact about this
    // network, and a user who is told the list is empty should not have
    // to sit through another thirty seconds to be told it again.
    private var scanCameBackEmpty = false

    // Guards the button while a login is in flight. The button stays
    // enabled (a disabled MaterialButton greys out, which looks wrong
    // under a spinner) so clicks are ignored here instead.
    private var loading = false

    // Required (API 33+) for MixerConnectionService's ongoing notification
    // to actually be visible - best-effort, the foreground service (and so
    // the background-survival fix it provides) still runs without it.
    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    // Top system-bar inset, cached from the last insets pass so the banner
    // offset can be recomputed on a width change without waiting for another.
    private var statusBarInset = 0

    // How far down the form starts: a fixed share of the screen, which is the
    // strip of wallpaper the form deliberately stays off - the hanging lights
    // across the top. Off the screen's height rather than the artwork's own
    // proportions, because the picture now fills the window instead of
    // heading it, and the same number goes to the background, which starts
    // its frost just above the form (see LoginBackgroundView).
    //
    // Not added to the status bar inset but maxed against it: the wallpaper
    // is drawn from y=0, status bar included, so adding the two would leave a
    // bar-sized hole above the form on a phone with a tall cutout.
    private fun contentTopFor(screenHeight: Int): Int {
        val height = if (screenHeight > 0) {
            screenHeight
        } else {
            resources.displayMetrics.heightPixels
        }
        return max(
            statusBarInset,
            (height * LoginBackgroundView.CONTENT_TOP_FRACTION).toInt()
        )
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        binding = ActivityConnectBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // The wallpaper runs to the very top of the window, under the status
        // bar, so what the clock and icons sit on is the artwork rather than
        // the window background - and which artwork that is now follows the
        // theme (see LoginBackgroundView), so they go dark over the day cut's
        // pale wall and stay white over the night one.
        WindowCompat.getInsetsController(window, window.decorView)
            .isAppearanceLightStatusBars = !isNightMode()

        // Android 15+ (targetSdk 35+) draws this activity edge-to-edge by
        // default now - pad the scrolling content by the system bar insets
        // (on top of its own 28dp padding) so the form isn't under the
        // status bar and the button isn't under the nav bar/gesture strip.
        // The top inset is folded into the banner offset below rather than
        // applied here, because the banner is what actually sits under the
        // status bar.
        val scrollBasePaddingBottom = binding.formScroll.paddingBottom
        val contentBasePadding = Rect(
            binding.content.paddingLeft, binding.content.paddingTop,
            binding.content.paddingRight, binding.content.paddingBottom
        )
        ViewCompat.setOnApplyWindowInsetsListener(binding.content) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            statusBarInset = bars.top

            view.setPadding(
                contentBasePadding.left + bars.left,
                contentBasePadding.top + contentTopFor(binding.loginBackground.height),
                contentBasePadding.right + bars.right,
                contentBasePadding.bottom + bars.bottom
            )

            // The floating control is a child of the window-sized root,
            // so nothing else has moved it clear of the gesture strip.
            (binding.themeToggle.layoutParams as FrameLayout.LayoutParams).apply {
                val base = dpToPx(20)
                if (bottomMargin != base + bars.bottom || marginEnd != base + bars.right) {
                    bottomMargin = base + bars.bottom
                    marginEnd = base + bars.right
                    binding.themeToggle.requestLayout()
                }
            }

            // The keyboard's height goes on the ScrollView, not on the
            // content inside it. This screen draws edge-to-edge, so the
            // window does not resize when the IME opens - the ScrollView
            // still believes its viewport is the whole screen, decides
            // the password field is already visible, and scrolls
            // nowhere. Padding the scrolling container is what tells it
            // that its bottom 850px are behind the keyboard, which is
            // both what makes room and what makes requestRectangleOnScreen
            // below actually move.
            binding.formScroll.setPadding(
                binding.formScroll.paddingLeft,
                binding.formScroll.paddingTop,
                binding.formScroll.paddingRight,
                scrollBasePaddingBottom + ime.bottom
            )

            imeInsetBottom = ime.bottom
            val keyboardOpen = ime.bottom > 0

            if (keyboardOpen && !imeWasOpen) {
                // Padding alone only makes room; the field still has to be
                // brought into it - and only once the padding above has
                // actually been laid out. post() is too early: it runs
                // before the layout pass, so the ScrollView still measures
                // its old, shorter content and concludes there is nowhere
                // to scroll. doOnNextLayout runs after it, when the scroll
                // range reflects the keyboard.
                view.doOnNextLayout { scrollFocusedFieldIntoView() }
            }

            imeWasOpen = keyboardOpen
            insets
        }
        // The screen's height isn't known when the insets first arrive, and
        // it is stale whenever insets land ahead of a layout at a new size.
        // Locking and unlocking the phone can do this: the form came back
        // pushed down by exactly the landscape/portrait ratio, i.e. the
        // insets pass padded for a landscape-height screen and nothing ever
        // put it back. So every layout checks the offset against the size the
        // window was actually laid out at, rather than only reacting when it
        // changes: setPadding is a no-op when nothing differs, so this
        // settles after one extra pass at most.
        binding.content.addOnLayoutChangeListener { view, _, _, _, _, _, _, _, _ ->
            val expectedTop =
                contentBasePadding.top + contentTopFor(binding.loginBackground.height)
            if (view.paddingTop != expectedTop) {
                view.setPadding(
                    view.paddingLeft,
                    expectedTop,
                    view.paddingRight,
                    view.paddingBottom
                )
            }
            // The band is pinned to the box it sits behind rather than to any
            // fixed height. Discovered's top is already in the background's
            // own coordinates: content and wallpaper share a parent, the
            // content sits at its top-left, and the band is deliberately
            // measured at rest - it marks where the controls are on screen,
            // and stays put when the form scrolls through it.
            binding.loginBackground.setBandTop(binding.discoveredContainer.top.toFloat())
        }

        prefs = getSharedPreferences("connection", MODE_PRIVATE)
        val host = prefs.getString("host", "") ?: ""
        val port = prefs.getString("port", "8765") ?: "8765"
        binding.hostInput.setText(host)
        binding.portInput.setText(port)
        binding.usernameInput.setText(prefs.getString("username", ""))

        // Username/password stay disabled, and the address fields hidden
        // behind Manual, until the user has actually picked a server
        // (manually or via discovery) - always, even for a returning user
        // with a remembered host, so Manual is never unexpectedly missing.
        setCredentialsEnabled(false)

        binding.discoveredRefresh.setOnClickListener { rescan() }

        // REMOVE WITH DEMO MODE.
        binding.demoContainer.setOnClickListener { if (!loading) enterDemoMode() }

        // The one place the theme can be set before there is a mixer
        // screen to set it from - useful for a phone that has been
        // handed to someone, or for an operator who wants the light
        // palette up before the lights go down. This screen now answers
        // it too, wallpaper included, which costs the usual recreate:
        // setDefaultNightMode rebuilds whatever is running, so the form
        // comes back as it would on a fresh launch. The drawer's switch
        // stays as it is and the two agree, since both read and write
        // ThemeStore.
        updateThemeToggle()
        binding.themeToggle.setOnClickListener {
            ThemeStore.setDarkMode(this, !ThemeStore.isDarkMode(this))
            updateThemeToggle()
        }

        binding.manualButton.setOnClickListener {
            val expanding = binding.manualFields.visibility != View.VISIBLE
            setManualFieldsExpanded(expanding)
            setCredentialsEnabled(expanding)
            if (expanding) binding.hostInput.requestFocus()
        }

        buttonBackground = binding.connectButton.backgroundTintList
        setUpButtonLabel()

        binding.usernameInput.addTextChangedListener(clearErrorOnType())
        binding.passwordInput.addTextChangedListener(clearErrorOnType())

        binding.connectButton.setOnClickListener {
            if (!loading) attemptConnect()
        }

        // Enter on the password field submits, the way it already moves
        // from username to password. Goes through the same guard and the
        // same call as the button so there is one login path, not two
        // that could drift - in particular a second attempt cannot be
        // started while one is already in flight.
        binding.passwordInput.setOnEditorActionListener { _, actionId, event ->
            val submitted = actionId == EditorInfo.IME_ACTION_DONE ||
                actionId == EditorInfo.IME_ACTION_GO ||
                // A hardware or Bluetooth keyboard sends the key itself
                // rather than an IME action id. ACTION_DOWN only, or the
                // matching ACTION_UP would submit a second time.
                (event?.keyCode == KeyEvent.KEYCODE_ENTER &&
                    event.action == KeyEvent.ACTION_DOWN)

            if (submitted && !loading) {
                attemptConnect()
            }

            submitted
        }

        mdnsDiscovery = MdnsDiscovery(this)

        requestNotificationPermission()

        // Silently resume a previous session instead of making the user
        // log in again every time the app is relaunched after being fully
        // closed (see SessionStore) - only when we're not already
        // connected, so navigating back here from AuxListActivity/
        // MixerActivity doesn't tear down a perfectly good live socket.
        val token = SessionStore.getToken(this)
        if (token != null && host.isNotEmpty() && !MixerClient.isConnected) {
            pendingToken = token
            setLoading(true)
            showStatus("Reconnecting...")
            MixerClient.connect(host, port.toIntOrNull() ?: return)
        }
    }

    // The button stays put (unlike the old design, which hid it once
    // tapped) - it's now a fold/unfold toggle, so it needs to stick
    // around to fold the fields back again.
    private fun setManualFieldsExpanded(expanded: Boolean) {
        if ((binding.manualFields.visibility == View.VISIBLE) == expanded) return
        TransitionManager.beginDelayedTransition(binding.content, AutoTransition())
        binding.manualFields.visibility = if (expanded) View.VISIBLE else View.GONE
        // Folding the fields away withdraws the one reason Manual stays
        // on screen while a server is listed.
        updateManualVisibility()
    }

    private fun setCredentialsEnabled(enabled: Boolean) {
        binding.usernameInput.isEnabled = enabled
        binding.passwordInput.isEnabled = enabled
    }

    // Progress ("Connecting...", "Logging in...") - ordinary text, and
    // never reddens the fields, since nothing is wrong with them.
    private fun showStatus(message: String) {
        showingError = false
        setMessage(message, ContextCompat.getColor(this, R.color.on_surface_variant))
        setCredentialsRejected(false)
    }

    // rejectedCredentials marks the two fields as the thing to fix. It's
    // false for failures the typed username/password had nothing to do
    // with - an unreachable server, a snapshot the account isn't allowed
    // on - which still show in red here but leave the fields alone.
    private fun showError(message: String, rejectedCredentials: Boolean = false) {
        showingError = true
        setMessage(message, ContextCompat.getColor(this, R.color.mute_active))
        setCredentialsRejected(rejectedCredentials)
    }

    private fun clearMessage() {
        showingError = false
        setMessage("", 0)
        setCredentialsRejected(false)
    }

    private fun setMessage(message: String, color: Int) {
        val visibility = if (message.isEmpty()) View.GONE else View.VISIBLE

        if (binding.messageLabel.visibility != visibility) {
            TransitionManager.beginDelayedTransition(binding.content, AutoTransition())
        }

        binding.messageLabel.text = message
        binding.messageLabel.visibility = visibility

        if (message.isNotEmpty()) {
            binding.messageLabel.setTextColor(color)
        }
    }

    private fun setCredentialsRejected(rejected: Boolean) {
        val strokeColor = ContextCompat.getColorStateList(
            this,
            if (rejected) R.color.text_input_stroke_error else R.color.text_input_stroke
        )

        binding.usernameLayout.setBoxStrokeColorStateList(strokeColor!!)
        binding.passwordLayout.setBoxStrokeColorStateList(strokeColor)

        credentialsRejected = rejected
    }

    // Typing into either field is the user acting on the rejection, so
    // the whole error state - red borders and the message above them -
    // clears on the first keystroke in either box rather than lingering
    // while they retype.
    // The button's label is a TextSwitcher rather than the button's own
    // text so it can slide: each change animates the outgoing label out
    // one edge while the incoming one arrives from the other.
    private fun setUpButtonLabel() {
        binding.connectLabel.setFactory {
            TextView(this).apply {
                // Full height, centered text: the switcher is the
                // button's height, and the slide animations move each
                // label by 100% of its own height - so filling it is
                // what makes a label travel exactly one button's worth
                // as it enters or leaves.
                layoutParams = FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT
                )
                gravity = Gravity.CENTER
                textSize = 16f
                setTextColor(ContextCompat.getColor(this@ConnectActivity, R.color.on_primary))
                typeface = android.graphics.Typeface.DEFAULT_BOLD
            }
        }

        binding.connectLabel.setCurrentText(LABEL_LOGIN)
        binding.connectButton.contentDescription = LABEL_LOGIN
    }

    // rising = the new label comes up from the bottom (a result arriving);
    // falling = it drops in from the top (going back to "Login").
    private fun setButtonLabel(label: String, rising: Boolean) {
        binding.connectLabel.setInAnimation(
            this, if (rising) R.anim.button_label_in_up else R.anim.button_label_in_down
        )
        binding.connectLabel.setOutAnimation(
            this, if (rising) R.anim.button_label_out_up else R.anim.button_label_out_down
        )

        binding.connectLabel.setText(label)
        binding.connectButton.contentDescription = label
    }

    // Cross-fades the fill instead of snapping between blue and red.
    private fun animateButtonColor(toColor: Int, restore: ColorStateList? = null) {
        val fromColor = binding.connectButton.backgroundTintList?.defaultColor ?: toColor

        colorAnimator?.cancel()

        if (fromColor == toColor) {
            restore?.let { binding.connectButton.backgroundTintList = it }
            return
        }

        colorAnimator = ValueAnimator.ofObject(ArgbEvaluator(), fromColor, toColor).apply {
            duration = COLOR_FADE_MS

            addUpdateListener { animator ->
                binding.connectButton.backgroundTintList =
                    ColorStateList.valueOf(animator.animatedValue as Int)
            }

            doOnEnd {
                // Put the original multi-state list back once the fade has
                // landed on its color - the flat one used while animating
                // has no pressed/disabled states of its own.
                restore?.let { binding.connectButton.backgroundTintList = it }
            }

            start()
        }
    }

    private fun setLoading(isLoading: Boolean) {
        loading = isLoading

        // Set without animation either way - the label isn't sliding
        // anywhere here. Going in it clears out so the spinner has the
        // button to itself; coming back out it restores "Login", which
        // matters for the paths that stop loading without any result to
        // show (a rejected password, an expired saved session): they'd
        // otherwise leave the button blank. When a result *is* coming,
        // showButtonResult has already claimed the label and animates
        // over this.
        if (isLoading) {
            binding.connectLabel.setCurrentText("")
        } else if (!showingButtonResult) {
            binding.connectLabel.setCurrentText(LABEL_LOGIN)
        }

        binding.connectButton.contentDescription = LABEL_LOGIN
        binding.connectProgress.visibility = if (isLoading) View.VISIBLE else View.GONE
    }

    // Reports a failure on the button itself - the spinner gives way to a
    // short label, which holds briefly and then turns back into "Login"
    // so the user can just try again.
    private fun showButtonResult(label: String, isRejection: Boolean) {
        setLoading(false)
        resultHandler.removeCallbacksAndMessages(null)

        showingButtonResult = true
        setButtonLabel(label, rising = true)

        if (isRejection) {
            animateButtonColor(ContextCompat.getColor(this, R.color.mute_active))
        }

        resultHandler.postDelayed({ resetButton() }, RESULT_HOLD_MS)
    }

    private fun resetButton() {
        resultHandler.removeCallbacksAndMessages(null)

        // Only animate back if something was actually showing - otherwise
        // a plain re-entry to the screen would slide "Login" in over
        // itself for no reason.
        if (showingButtonResult) {
            setButtonLabel(LABEL_LOGIN, rising = false)
        } else {
            binding.connectLabel.setCurrentText(LABEL_LOGIN)
            binding.connectButton.contentDescription = LABEL_LOGIN
        }

        showingButtonResult = false

        buttonBackground?.let { animateButtonColor(it.defaultColor, restore = it) }
    }

    private fun clearErrorOnType(): TextWatcher = object : TextWatcher {
        override fun afterTextChanged(s: Editable?) {
            if (credentialsRejected) clearMessage()
        }

        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return

        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED

        if (!granted) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    override fun onResume() {
        super.onResume()
        MixerClient.claimListener(this)
        mdnsDiscovery.start(this)
        openScanWindow()
    }

    override fun onPause() {
        super.onPause()
        MixerClient.releaseListener(this)
        mdnsDiscovery.stop()
        // The window closes with the browse that feeds it rather than
        // running on in the background: a deadline that expired while
        // the app was away would otherwise decide, on nothing, that the
        // network has no servers on it.
        scanHandler.removeCallbacksAndMessages(null)
        setScanning(false)
    }

    /**
     * Throws the list away and browses again from nothing, rather than
     * leaving what is already there and waiting for more: a stale row for
     * a desktop that has since been shut down is the one thing a refresh
     * is for, and mDNS only reports a service going away while the browse
     * that found it is still running.
     *
     * The selection goes with it - the row it pointed at is gone - but the
     * host and port it filled in stay, so a login already set up is not
     * undone by asking the network again.
     */
    private fun rescan() {
        selectedRow = null
        discoveredRows.clear()
        TransitionManager.beginDelayedTransition(binding.content, AutoTransition())
        binding.discoveredServers.removeAllViews()
        binding.discoveredServers.visibility = View.GONE
        updateManualVisibility()

        // start() tears down any browse already running, so there is no
        // stop() to pair with this.
        mdnsDiscovery.start(this)
        openScanWindow()
    }

    /** Spinner on, deadline armed. Idempotent - every path that starts a
     *  browse calls it, and a second call replaces the first's deadline
     *  rather than leaving two of them to fire. */
    private fun openScanWindow() {
        scanHandler.removeCallbacksAndMessages(null)
        setScanning(true)
        scanHandler.postDelayed({ closeScanWindow() }, SCAN_WINDOW_MS)
    }

    private fun closeScanWindow() {
        setScanning(false)
        if (discoveredRows.isNotEmpty()) return

        scanCameBackEmpty = true
        updateManualVisibility()
    }

    /**
     * Whether Manual is on screen at all. Typing an address is the
     * fallback for a network where announcement does not work, not a
     * second way of doing what the list above already does - so it
     * appears only once a scan has come back with nothing, and goes away
     * again the moment a server does turn up. Worked out from the two
     * facts every time rather than latched on and off, so a server
     * appearing, going away and coming back is handled by the same
     * expression rather than by three separate edges.
     *
     * The exception is a user already typing into it: a form that
     * vanishes mid-address because the desktop finally announced itself
     * is worse than one that stays a moment too long. Picking a
     * discovered server folds the fields away, which takes this with it.
     */
    private fun updateManualVisibility() {
        val expanded = binding.manualFields.visibility == View.VISIBLE
        val show = scanCameBackEmpty && (discoveredRows.isEmpty() || expanded)
        val visibility = if (show) View.VISIBLE else View.GONE
        if (binding.manualContainer.visibility == visibility) return

        TransitionManager.beginDelayedTransition(binding.content, AutoTransition())
        binding.manualContainer.visibility = visibility
        binding.manualDivider.visibility = visibility
    }

    private fun updateThemeToggle() {
        val dark = ThemeStore.isDarkMode(this)
        binding.themeToggle.setImageResource(
            if (dark) R.drawable.ic_dark_mode else R.drawable.ic_light_mode
        )
        binding.themeToggle.contentDescription =
            if (dark) "Switch to light mode" else "Switch to dark mode"
    }

    private fun setScanning(scanning: Boolean) {
        binding.discoveredProgress.visibility = if (scanning) View.VISIBLE else View.GONE
        binding.discoveredRefresh.visibility = if (scanning) View.GONE else View.VISIBLE
    }

    override fun onDestroy() {
        super.onDestroy()
        // Nothing left to put back once this activity is gone, and the
        // posted callback would outlive it.
        resultHandler.removeCallbacksAndMessages(null)
        scanHandler.removeCallbacksAndMessages(null)
    }

    /**
     * Scrolls whichever field has focus clear of the keyboard.
     *
     * requestRectangleOnScreen walks up to the ScrollView, so this works
     * for any focused field rather than naming the password one - and it
     * does nothing when the field is already visible.
     */
    // Whether the keyboard was up on the previous insets pass, so the
    // scroll runs once when it opens rather than on every frame of the
    // IME animation, which would fight a user scrolling by hand.
    private var imeWasOpen = false

    // Height of the keyboard as last reported, so the scroll below knows
    // how much of the ScrollView is actually behind it.
    private var imeInsetBottom = 0

    private fun scrollFocusedFieldIntoView() {
        val focused = currentFocus ?: return
        val scroller = binding.formScroll

        // Worked out explicitly rather than left to
        // requestRectangleOnScreen: this window does not resize for the
        // keyboard, so the ScrollView's own idea of "on screen" includes
        // the strip the keyboard is sitting on and it concludes there is
        // nothing to do.
        val fieldLocation = IntArray(2)
        val scrollerLocation = IntArray(2)
        focused.getLocationInWindow(fieldLocation)
        scroller.getLocationInWindow(scrollerLocation)

        val fieldBottomInScroller =
            fieldLocation[1] - scrollerLocation[1] + focused.height
        val visibleHeight = scroller.height - imeInsetBottom
        val overlap = fieldBottomInScroller - visibleHeight + FIELD_SCROLL_MARGIN_PX

        if (overlap > 0) {
            scroller.smoothScrollBy(0, overlap)
        }
    }

    private fun attemptConnect() {
        val host = binding.hostInput.text.toString().trim()
        val portText = binding.portInput.text.toString().trim()
        val username = binding.usernameInput.text.toString().trim()
        val password = binding.passwordInput.text.toString()

        if (host.isEmpty() || portText.isEmpty()) {
            Toast.makeText(this, "Enter host and port", Toast.LENGTH_SHORT).show()
            return
        }

        val port = portText.toIntOrNull()
        if (port == null) {
            Toast.makeText(this, "Invalid port", Toast.LENGTH_SHORT).show()
            return
        }

        if (username.isEmpty() || password.isEmpty()) {
            Toast.makeText(this, "Enter username and password", Toast.LENGTH_SHORT).show()
            return
        }

        prefs.edit()
            .putString("host", host)
            .putString("port", portText)
            .putString("username", username)
            .apply()

        pendingUsername = username
        pendingPassword = password
        pendingToken = null

        resetButton()
        setLoading(true)
        showStatus("Connecting...")
        MixerClient.connect(host, port)
    }

    /**
     * Starts a local demo session: no server, no console, no network.
     * Goes through exactly the same connect -> login -> auxes sequence a
     * real session does, so nothing downstream of here knows the
     * difference - the demo answers on the wire protocol, in MixerClient
     * (see DemoMixer).
     *
     * REMOVE WITH DEMO MODE.
     */
    private fun enterDemoMode() {
        pendingUsername = "demo"
        pendingPassword = "demo"
        pendingToken = null

        resetButton()
        setLoading(true)
        showStatus("Starting demo...")
        MixerClient.connect(DemoMixer.HOST, 0)
    }

    override fun onConnected() {
        val token = pendingToken
        if (token != null) {
            showStatus("Reconnecting...")
            MixerClient.loginWithToken(token)
        } else {
            showStatus("Logging in...")
            MixerClient.login(pendingUsername, pendingPassword)
        }
    }

    override fun onLoginResult(ok: Boolean, message: String?, token: String?) {
        if (ok) {
            // Stays loading until onAuxes navigates away - or until the
            // server rejects the aux list because of the snapshot scope,
            // which arrives at onError below.
            showStatus("Connected")
            if (token != null) {
                SessionStore.saveToken(this, token)
            }

            // An account holding Full Mixer Control is asked which of the
            // two it wants before anything is selected; everyone else
            // goes straight on to the aux list, which is the only flow
            // that existed before that permission.
            if (MixerClient.mixerControlAllowed) {
                startActivity(Intent(this, ControlChoiceActivity::class.java))
            } else {
                MixerClient.requestAuxes()
            }
        } else {
            val wasTokenAttempt = pendingToken != null
            pendingToken = null

            if (wasTokenAttempt) {
                // The saved session is no longer valid (most likely the
                // desktop app restarted since it was issued) - drop it so
                // the next launch doesn't keep retrying it, and fall back
                // to the ordinary login form already on screen.
                SessionStore.clear(this)
            }

            setLoading(false)

            if (wasTokenAttempt) {
                // The saved session expired rather than anything being
                // wrong with what's typed on screen - most often the
                // desktop app restarted. Nothing for the user to correct,
                // so this doesn't mark the fields.
                clearMessage()
            } else {
                showError(
                    message ?: "Invalid username or password",
                    rejectedCredentials = true
                )
            }

            MixerClient.disconnect()
        }
    }

    override fun onDisconnected() {
        // Don't talk over a failure that just closed this socket itself -
        // both the message line and the button are already reporting it.
        if (showingError || showingButtonResult) return

        setLoading(false)
        clearMessage()
    }

    // The socket never opened at all (bad host, refused, timed out, ...).
    // The socket never opened - bad address, server not running, wrong
    // network. The specific cause isn't actionable from here, so the
    // button says so plainly for a moment and then offers itself again.
    override fun onConnectionFailed(message: String) {
        clearMessage()
        showButtonResult("Can't Reach Server", isRejection = false)
    }

    // We were connected and logged in, but the next step (e.g. fetching
    // the aux list right after login) came back rejected - most commonly
    // this account's snapshot doesn't match the one currently live on the
    // console. Nothing left to do at this point but let the user retry,
    // so disconnect cleanly rather than leaving a dead login session
    // sitting behind the connect screen.
    override fun onError(message: String) {
        if (message == MixerClient.SNAPSHOT_DENIED) {
            // A standing permissions problem, not a transient failure:
            // this account is scoped to a different snapshot than the one
            // live on the console right now. Called out in red on the
            // button, since retrying as-is will fail the same way.
            clearMessage()
            showButtonResult("No Snapshot Access", isRejection = true)
        } else {
            // Anything else the server rejected is a full sentence
            // already, and too long for the button - it goes on the
            // message line instead.
            setLoading(false)
            showError(message)
        }

        MixerClient.disconnect()
    }

    override fun onAuxes(auxes: List<AuxBus>) {
        val intent = Intent(this, AuxListActivity::class.java)
        intent.putExtra("auxes", ArrayList(auxes))
        startActivity(intent)
    }

    // Tapping a row fills in the (still-hidden) address fields, unlocks
    // username/password now that there's actually a server to log into,
    // and leaves it to the user to type credentials and press Connect -
    // it doesn't auto-connect, since that'd log in without the user
    // explicitly choosing this server when more than one is on the
    // network.
    override fun onServerFound(server: DiscoveredServer) {
        val existing = discoveredRows[server.name]

        if (existing != null) {
            existing.text = server.name
            existing.tag = server
            return
        }

        // Outlined (transparent background, white border) like
        // manual_button/discovered_label until tapped - only the row the
        // user actually picks fills in with the app's blue accent, so the
        // selection itself is what stands out rather than every row.
        val row = MaterialButton(this).apply {
            text = server.name
            tag = server
            textSize = 16f
            gravity = Gravity.CENTER
            elevation = 0f
            cornerRadius = dpToPx(8)
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = dpToPx(8) }
            setRowSelected(this, selected = false)
            setOnClickListener {
                val tagged = tag as? DiscoveredServer ?: return@setOnClickListener

                selectedRow?.let { setRowSelected(it, selected = false) }
                setRowSelected(this, selected = true)
                selectedRow = this

                setManualFieldsExpanded(false)
                binding.hostInput.setText(tagged.host)
                binding.portInput.setText(tagged.port.toString())
                setCredentialsEnabled(true)
                binding.usernameInput.requestFocus()
            }
        }

        // "Discovered" itself is always on screen - only the list under it
        // (and so the border wrapping both) grows/shrinks as servers come
        // and go.
        TransitionManager.beginDelayedTransition(binding.content, AutoTransition())
        discoveredRows[server.name] = row
        binding.discoveredServers.addView(row)
        binding.discoveredServers.visibility = View.VISIBLE

        // There is now something in the list, so the fallback under it
        // has nothing to be a fallback for.
        updateManualVisibility()
    }

    override fun onServerLost(name: String) {
        val row = discoveredRows.remove(name) ?: return

        if (row === selectedRow) selectedRow = null

        TransitionManager.beginDelayedTransition(binding.content, AutoTransition())
        binding.discoveredServers.removeView(row)

        if (discoveredRows.isEmpty()) {
            binding.discoveredServers.visibility = View.GONE
        }

        // The last server going away puts Manual back, if a scan had
        // already come back empty once.
        updateManualVisibility()
    }

    // Unselected: transparent fill with a plain outline, matching the
    // discovered/manual boxes around it. Selected: filled with the app's
    // blue accent (colorPrimary, via its day/night color resources) so the
    // chosen server stands out. Every color here comes from the day/night
    // resources rather than a literal, since the drawer's Dark mode switch
    // means either palette can be in force on this screen.
    private fun setRowSelected(row: MaterialButton, selected: Boolean) {
        if (selected) {
            row.strokeWidth = 0
            row.backgroundTintList = ColorStateList.valueOf(
                ContextCompat.getColor(row.context, R.color.primary)
            )
            row.setTextColor(ContextCompat.getColor(row.context, R.color.on_primary))
        } else {
            row.strokeWidth = dpToPx(1)
            row.strokeColor = ColorStateList.valueOf(
                ContextCompat.getColor(row.context, R.color.login_outline)
            )
            row.backgroundTintList = ColorStateList.valueOf(Color.TRANSPARENT)
            row.setTextColor(ContextCompat.getColor(row.context, R.color.on_surface))
        }
    }

    private fun dpToPx(dp: Int): Int = (dp * resources.displayMetrics.density).toInt()

    // This activity's own configuration rather than ThemeStore's saved
    // choice: with nothing saved the app follows the phone, and it is what
    // the window was actually inflated against that the status bar icons
    // have to agree with.
    private fun isNightMode(): Boolean =
        resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK ==
            Configuration.UI_MODE_NIGHT_YES

    companion object {
        /**
         * How long a scan runs before it gives up and offers Manual.
         * Announcement on a quiet network is usually answered inside a
         * second or two; this is long enough that a phone which has just
         * joined the Wi-Fi, or a desktop still starting up, is not
         * written off, and short enough that somebody standing at the
         * console with an address in their hand is not left waiting on
         * it. Matches the iOS ConnectView's own scan window.
         */
        private const val SCAN_WINDOW_MS = 30_000L

        // How long a failure stays on the button before it turns back
        // into "Login".
        private const val RESULT_HOLD_MS = 5000L
        private const val COLOR_FADE_MS = 250L
        // A little breathing room above the keyboard rather than the field
// sitting flush against it.
private const val FIELD_SCROLL_MARGIN_PX = 48

private const val LABEL_LOGIN = "Login"
    }
}
