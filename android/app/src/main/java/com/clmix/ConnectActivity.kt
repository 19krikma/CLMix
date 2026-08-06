package com.clmix

import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.clmix.databinding.ActivityConnectBinding

class ConnectActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityConnectBinding
    private lateinit var prefs: SharedPreferences

    // Stashed from the form at connect time so onConnected() can log in
    // once the socket is open, without persisting the password to disk.
    private var pendingUsername: String = ""
    private var pendingPassword: String = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConnectBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = getSharedPreferences("connection", MODE_PRIVATE)
        binding.hostInput.setText(prefs.getString("host", ""))
        binding.portInput.setText(prefs.getString("port", "8765"))
        binding.usernameInput.setText(prefs.getString("username", ""))

        binding.connectButton.setOnClickListener { attemptConnect() }
    }

    override fun onResume() {
        super.onResume()
        MixerClient.listener = this
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

        binding.connectButton.isEnabled = false
        binding.statusLabel.text = "Connecting..."
        MixerClient.connect(host, port)
    }

    override fun onConnected() {
        binding.statusLabel.text = "Logging in..."
        MixerClient.login(pendingUsername, pendingPassword)
    }

    override fun onLoginResult(ok: Boolean, message: String?) {
        if (ok) {
            binding.statusLabel.text = "Connected"
            MixerClient.requestAuxes()
        } else {
            binding.connectButton.isEnabled = true
            binding.statusLabel.text = "Login failed: ${message ?: "Invalid username or password"}"
            MixerClient.disconnect()
        }
    }

    override fun onDisconnected() {
        binding.connectButton.isEnabled = true
        binding.statusLabel.text = "Disconnected"
    }

    override fun onError(message: String) {
        binding.connectButton.isEnabled = true
        binding.statusLabel.text = "Error: $message"
    }

    override fun onAuxes(auxes: List<AuxBus>) {
        val intent = Intent(this, AuxListActivity::class.java)
        intent.putExtra("auxes", ArrayList(auxes))
        startActivity(intent)
    }
}
