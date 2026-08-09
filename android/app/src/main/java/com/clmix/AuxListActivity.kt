package com.clmix

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.clmix.databinding.ActivityAuxListBinding
import com.clmix.databinding.ItemAuxBinding

class AuxListActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityAuxListBinding
    private var auxes: ArrayList<AuxBus> = arrayListOf()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityAuxListBinding.inflate(layoutInflater)
        setContentView(binding.root)

        @Suppress("UNCHECKED_CAST")
        auxes = intent.getSerializableExtra("auxes") as? ArrayList<AuxBus> ?: arrayListOf()

        binding.auxRecycler.layoutManager = LinearLayoutManager(this)
        binding.auxRecycler.adapter = AuxAdapter(auxes) { aux -> openMixer(aux) }
    }

    override fun onResume() {
        super.onResume()
        MixerClient.listener = this
    }

    private fun openMixer(aux: AuxBus) {
        val intent = Intent(this, MixerActivity::class.java)
        intent.putExtra("auxIndex", aux.index)
        intent.putExtra("auxName", aux.name)
        intent.putExtra("auxes", auxes)
        startActivity(intent)
    }

    private fun returnToLogin() {
        val intent = Intent(this, ConnectActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        startActivity(intent)
    }

    override fun onDisconnected() {
        returnToLogin()
    }

    override fun onConnectionFailed(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        returnToLogin()
    }

    override fun onError(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}

class AuxAdapter(
    private val items: List<AuxBus>,
    private val onClick: (AuxBus) -> Unit
) : RecyclerView.Adapter<AuxAdapter.ViewHolder>() {

    class ViewHolder(val binding: ItemAuxBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemAuxBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        holder.binding.auxName.text = item.name
        holder.binding.root.setOnClickListener { onClick(item) }
    }

    override fun getItemCount() = items.size
}
