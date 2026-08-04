package com.digico.monitormix

import android.content.Intent
import android.content.res.ColorStateList
import android.os.Bundle
import android.widget.AdapterView
import android.widget.ArrayAdapter
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.recyclerview.widget.LinearLayoutManager
import com.digico.monitormix.databinding.ActivityMixerBinding

class MixerActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityMixerBinding
    private lateinit var adapter: ChannelAdapter
    private var auxIndex: Int = -1
    private var smoothEnabled = false
    private val draggingChannels = mutableSetOf<Int>()
    private val panDraggingChannels = mutableSetOf<Int>()
    private val dragReleasedAt = mutableMapOf<Int, Long>()
    private val panDragReleasedAt = mutableMapOf<Int, Long>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMixerBinding.inflate(layoutInflater)
        setContentView(binding.root)
        enterFullScreen()

        auxIndex = intent.getIntExtra("auxIndex", -1)
        title = intent.getStringExtra("auxName") ?: "Aux"

        @Suppress("UNCHECKED_CAST")
        val auxes = intent.getSerializableExtra("auxes") as? ArrayList<AuxBus> ?: arrayListOf()

        adapter = ChannelAdapter(
            onLevelChanged = { channel, db -> MixerClient.setLevel(channel, db) },
            onPanChanged = { channel, pan -> MixerClient.setPan(channel, pan) },
            onMuteToggled = { channel, muted -> MixerClient.setMute(channel, muted) },
            onDragStart = { channel -> draggingChannels.add(channel) },
            onDragEnd = { channel ->
                draggingChannels.remove(channel)
                dragReleasedAt[channel] = System.currentTimeMillis()
            },
            onPanDragStart = { channel -> panDraggingChannels.add(channel) },
            onPanDragEnd = { channel ->
                panDraggingChannels.remove(channel)
                panDragReleasedAt[channel] = System.currentTimeMillis()
            }
        )

        binding.channelRecycler.layoutManager =
            LinearLayoutManager(this, LinearLayoutManager.HORIZONTAL, false)
        binding.channelRecycler.adapter = adapter

        binding.bankSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(
                parent: AdapterView<*>?, view: android.view.View?, position: Int, id: Long
            ) {
                val selected = parent?.getItemAtPosition(position) as? String ?: return
                MixerClient.selectBank(if (selected == "All") null else selected)
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        binding.menuButton.setOnClickListener {
            binding.drawerLayout.openDrawer(GravityCompat.START)
        }

        binding.drawerAuxRecycler.layoutManager = LinearLayoutManager(this)
        binding.drawerAuxRecycler.adapter = AuxAdapter(auxes) { aux -> switchAux(aux) }

        binding.logoutButton.setOnClickListener { logout() }

        binding.smoothButton.setOnClickListener {
            smoothEnabled = !smoothEnabled
            adapter.smoothEnabled = smoothEnabled
            updateSmoothButtonAppearance()
        }
        updateSmoothButtonAppearance()
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
        MixerClient.listener = this
        MixerClient.selectAux(auxIndex)
        MixerClient.requestBanks()
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

    private fun switchAux(aux: AuxBus) {
        binding.drawerLayout.closeDrawer(GravityCompat.START)

        if (aux.index == auxIndex) return

        auxIndex = aux.index
        title = aux.name
        MixerClient.selectAux(auxIndex)
    }

    private fun logout() {
        MixerClient.disconnect()
        returnToLogin()
    }

    private fun returnToLogin() {
        val intent = Intent(this, ConnectActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        startActivity(intent)
    }

    override fun onDisconnected() {
        binding.statusLabel.text = "Disconnected"
        returnToLogin()
    }

    override fun onError(message: String) {
        binding.statusLabel.text = "Error: $message"

        // Some errors are just protocol-level (e.g. "not permitted for this
        // aux") and don't mean the socket itself dropped. Only bounce back
        // to login when the connection is actually gone.
        if (!MixerClient.isConnected) {
            returnToLogin()
        }
    }

    override fun onBanks(banks: List<String>) {
        val items = listOf("All") + banks
        binding.bankSpinner.adapter =
            ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, items)
    }

    override fun onLevels(aux: Int, channels: List<ChannelState>) {
        if (aux != auxIndex) return
        adapter.updateChannels(
            channels, draggingChannels, panDraggingChannels,
            dragReleasedAt, panDragReleasedAt
        )
    }
}
