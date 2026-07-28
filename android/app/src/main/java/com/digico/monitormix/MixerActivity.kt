package com.digico.monitormix

import android.content.Intent
import android.os.Bundle
import android.widget.AdapterView
import android.widget.ArrayAdapter
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.GravityCompat
import androidx.recyclerview.widget.LinearLayoutManager
import com.digico.monitormix.databinding.ActivityMixerBinding

class MixerActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityMixerBinding
    private lateinit var adapter: ChannelAdapter
    private var auxIndex: Int = -1
    private val draggingChannels = mutableSetOf<Int>()
    private val panDraggingChannels = mutableSetOf<Int>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMixerBinding.inflate(layoutInflater)
        setContentView(binding.root)

        auxIndex = intent.getIntExtra("auxIndex", -1)
        title = intent.getStringExtra("auxName") ?: "Aux"

        @Suppress("UNCHECKED_CAST")
        val auxes = intent.getSerializableExtra("auxes") as? ArrayList<AuxBus> ?: arrayListOf()

        adapter = ChannelAdapter(
            onLevelChanged = { channel, db -> MixerClient.setLevel(channel, db) },
            onPanChanged = { channel, pan -> MixerClient.setPan(channel, pan) },
            onMuteToggled = { channel, muted -> MixerClient.setMute(channel, muted) },
            onDragStart = { channel -> draggingChannels.add(channel) },
            onDragEnd = { channel -> draggingChannels.remove(channel) },
            onPanDragStart = { channel -> panDraggingChannels.add(channel) },
            onPanDragEnd = { channel -> panDraggingChannels.remove(channel) }
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
    }

    override fun onResume() {
        super.onResume()
        MixerClient.listener = this
        MixerClient.selectAux(auxIndex)
        MixerClient.requestBanks()
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

        val intent = Intent(this, ConnectActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        startActivity(intent)
    }

    override fun onDisconnected() {
        binding.statusLabel.text = "Disconnected"
    }

    override fun onError(message: String) {
        binding.statusLabel.text = "Error: $message"
    }

    override fun onBanks(banks: List<String>) {
        val items = listOf("All") + banks
        binding.bankSpinner.adapter =
            ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, items)
    }

    override fun onLevels(aux: Int, channels: List<ChannelState>) {
        if (aux != auxIndex) return
        adapter.updateChannels(channels, draggingChannels, panDraggingChannels)
    }
}
