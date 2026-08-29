package com.clmix

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.clmix.databinding.ActivityAuxListBinding
import com.clmix.databinding.ItemAuxBinding

class AuxListActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityAuxListBinding
    private var auxes: ArrayList<AuxBus> = arrayListOf()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        binding = ActivityAuxListBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Android 15+ (targetSdk 35+) draws this activity edge-to-edge by
        // default now - keep the title clear of the status bar, and grow
        // the recycler's existing bottom padding (it's already
        // clipToPadding=false, for the last row to stay reachable above
        // the nav bar/gesture strip) by the same amount.
        val titleBaseTopPadding = binding.auxTitle.paddingTop
        val recyclerBaseBottomPadding = binding.auxRecycler.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.auxTitle.setPadding(
                binding.auxTitle.paddingLeft, titleBaseTopPadding + bars.top,
                binding.auxTitle.paddingRight, binding.auxTitle.paddingBottom
            )
            binding.auxRecycler.setPadding(
                binding.auxRecycler.paddingLeft, binding.auxRecycler.paddingTop,
                binding.auxRecycler.paddingRight, recyclerBaseBottomPadding + bars.bottom
            )
            insets
        }

        @Suppress("UNCHECKED_CAST")
        auxes = intent.getSerializableExtra("auxes") as? ArrayList<AuxBus> ?: arrayListOf()

        binding.auxRecycler.layoutManager = LinearLayoutManager(this)
        binding.auxRecycler.adapter = AuxAdapter(auxes) { aux -> openMixer(aux) }

        // This is the last screen standing between here and ConnectActivity
        // (still sitting underneath in the back stack, still showing
        // itself as connected) - back-navigating past it without logging
        // out would land the user on a login screen that's silently still
        // logged in behind the scenes. Covers both the back button and the
        // gesture-nav swipe, unlike overriding onBackPressed().
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = logout()
        })
    }

    override fun onResume() {
        super.onResume()
        MixerClient.claimListener(this)
    }

    override fun onPause() {
        super.onPause()
        MixerClient.releaseListener(this)
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

    // Mirrors MixerActivity.logout() - revokes the session server-side and
    // drops the locally saved token before returning, so ConnectActivity
    // comes back as a genuine logged-out login screen instead of one that
    // silently still holds a live session underneath.
    private fun logout() {
        MixerClient.logout(SessionStore.getToken(this))
        SessionStore.clear(this)
        MixerClient.disconnect()
        returnToLogin()
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
