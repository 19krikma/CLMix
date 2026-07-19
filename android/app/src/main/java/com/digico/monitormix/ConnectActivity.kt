package com.digico.monitormix

import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.digico.monitormix.databinding.ActivityConnectBinding

class ConnectActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityConnectBinding
    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConnectBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = getSharedPreferences("connection", MODE_PRIVATE)
        binding.hostInput.setText(prefs.getString("host", ""))
        binding.portInput.setText(prefs.getString("port", "8765"))

        binding.connectButton.setOnClickListener { attemptConnect() }
    }

    override fun onResume() {
        super.onResume()
        MixerClient.listener = this
    }

    private fun attemptConnect() {
        val host = binding.hostInput.text.toString().trim()
        val portText = binding.portInput.text.toString().trim()

        if (host.isEmpty() || portText.isEmpty()) {
            Toast.makeText(this, "Enter host and port", Toast.LENGTH_SHORT).show()
            return
        }

        val port = portText.toIntOrNull()
        if (port == null) {
            Toast.makeText(this, "Invalid port", Toast.LENGTH_SHORT).show()
            return
        }

        prefs.edit().putString("host", host).putString("port", portText).apply()

        binding.connectButton.isEnabled = false
        binding.statusLabel.text = "Connecting..."
        MixerClient.connect(host, port)
    }

    override fun onConnected() {
        binding.statusLabel.text = "Connected"
        MixerClient.requestAuxes()
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
