package com.clmix

import android.Manifest
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.transition.AutoTransition
import android.transition.TransitionManager
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.clmix.databinding.ActivityConnectBinding
import com.google.android.material.button.MaterialButton

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

    // Required (API 33+) for MixerConnectionService's ongoing notification
    // to actually be visible - best-effort, the foreground service (and so
    // the background-survival fix it provides) still runs without it.
    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConnectBinding.inflate(layoutInflater)
        setContentView(binding.root)

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

        binding.manualButton.setOnClickListener {
            val expanding = binding.manualFields.visibility != View.VISIBLE
            setManualFieldsExpanded(expanding)
            setCredentialsEnabled(expanding)
            if (expanding) binding.hostInput.requestFocus()
        }

        binding.connectButton.setOnClickListener { attemptConnect() }

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
            binding.connectButton.isEnabled = false
            binding.statusLabel.text = "Reconnecting..."
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
    }

    private fun setCredentialsEnabled(enabled: Boolean) {
        binding.usernameInput.isEnabled = enabled
        binding.passwordInput.isEnabled = enabled
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
        MixerClient.listener = this
        mdnsDiscovery.start(this)
    }

    override fun onPause() {
        super.onPause()
        mdnsDiscovery.stop()
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

        binding.connectButton.isEnabled = false
        binding.statusLabel.text = "Connecting..."
        MixerClient.connect(host, port)
    }

    override fun onConnected() {
        val token = pendingToken
        if (token != null) {
            binding.statusLabel.text = "Reconnecting..."
            MixerClient.loginWithToken(token)
        } else {
            binding.statusLabel.text = "Logging in..."
            MixerClient.login(pendingUsername, pendingPassword)
        }
    }

    override fun onLoginResult(ok: Boolean, message: String?, token: String?) {
        if (ok) {
            binding.statusLabel.text = "Connected"
            if (token != null) {
                SessionStore.saveToken(this, token)
            }
            MixerClient.requestAuxes()
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

            binding.connectButton.isEnabled = true
            binding.statusLabel.text =
                message ?: if (wasTokenAttempt) "" else "Invalid username or password"
            MixerClient.disconnect()
        }
    }

    override fun onDisconnected() {
        binding.connectButton.isEnabled = true
        binding.statusLabel.text = "Disconnected"
    }

    // The socket never opened at all (bad host, refused, timed out, ...).
    override fun onConnectionFailed(message: String) {
        binding.connectButton.isEnabled = true
        binding.statusLabel.text = message
    }

    // We were connected and logged in, but the next step (e.g. fetching
    // the aux list right after login) came back rejected - most commonly
    // this account's snapshot doesn't match the one currently live on the
    // console. Nothing left to do at this point but let the user retry,
    // so disconnect cleanly rather than leaving a dead login session
    // sitting behind the connect screen.
    override fun onError(message: String) {
        binding.connectButton.isEnabled = true
        binding.statusLabel.text = message
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
    }

    override fun onServerLost(name: String) {
        val row = discoveredRows.remove(name) ?: return

        if (row === selectedRow) selectedRow = null

        TransitionManager.beginDelayedTransition(binding.content, AutoTransition())
        binding.discoveredServers.removeView(row)

        if (discoveredRows.isEmpty()) {
            binding.discoveredServers.visibility = View.GONE
        }
    }

    // Unselected: transparent fill with a white border, matching the
    // discovered/manual boxes around it. Selected: filled with the app's
    // blue accent (colorPrimary, via its day/night color resources) so the
    // chosen server stands out.
    private fun setRowSelected(row: MaterialButton, selected: Boolean) {
        if (selected) {
            row.strokeWidth = 0
            row.backgroundTintList = ColorStateList.valueOf(
                ContextCompat.getColor(row.context, R.color.primary)
            )
            row.setTextColor(ContextCompat.getColor(row.context, R.color.on_primary))
        } else {
            row.strokeWidth = dpToPx(1)
            row.strokeColor = ColorStateList.valueOf(Color.WHITE)
            row.backgroundTintList = ColorStateList.valueOf(Color.TRANSPARENT)
            row.setTextColor(Color.WHITE)
        }
    }

    private fun dpToPx(dp: Int): Int = (dp * resources.displayMetrics.density).toInt()
}
