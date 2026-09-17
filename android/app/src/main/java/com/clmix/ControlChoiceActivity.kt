package com.clmix

import android.content.Intent
import android.os.Bundle
import androidx.activity.OnBackPressedCallback
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.clmix.databinding.ActivityControlChoiceBinding

/**
 * The fork between mixing one aux send and mixing the console itself.
 *
 * Only accounts holding Full Mixer Control ever see this - ConnectActivity
 * sends everyone else straight on to the aux list, which is the flow that
 * existed before this screen. Picking "AUX Only" continues into exactly
 * that same flow, so the two paths differ only in where they start.
 */
class ControlChoiceActivity : AppCompatActivity(), MixerClientListener {
    private lateinit var binding: ActivityControlChoiceBinding

    // Set while waiting for the aux list that "AUX Only" asks for, so the
    // reply is only acted on when this screen actually asked for it - the
    // server pushes an aux list for other reasons too.
    private var awaitingAuxes = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        binding = ActivityControlChoiceBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Edge-to-edge on Android 15+: keep the title out from under the
        // status bar and the last card off the gesture strip.
        val titleBaseTopPadding = binding.choiceTitle.paddingTop
        val rootBaseBottomPadding = binding.root.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            binding.choiceTitle.setPadding(
                binding.choiceTitle.paddingLeft, titleBaseTopPadding + bars.top,
                binding.choiceTitle.paddingRight, binding.choiceTitle.paddingBottom
            )
            view.setPadding(
                view.paddingLeft, view.paddingTop,
                view.paddingRight, rootBaseBottomPadding + bars.bottom
            )
            insets
        }

        binding.auxOnlyCard.setOnClickListener {
            awaitingAuxes = true
            MixerClient.requestAuxes()
        }

        binding.mixerControlCard.setOnClickListener {
            MixerClient.selectMixerControl()
            startActivity(Intent(this, MixerControlActivity::class.java))
        }

        // Same reasoning as AuxListActivity: ConnectActivity is still
        // underneath in the back stack and still logged in, so backing
        // past this screen has to log out rather than appear to return to
        // a login form that is secretly already through.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = logout()
        })
    }

    override fun onResume() {
        super.onResume()

        // The socket can die while this screen is backgrounded, which
        // would leave two choices that both lead nowhere.
        if (!MixerClient.isConnected) {
            returnToLogin()
            return
        }

        MixerClient.claimListener(this)
    }

    override fun onPause() {
        super.onPause()
        MixerClient.releaseListener(this)
    }

    override fun onAuxes(auxes: List<AuxBus>) {
        if (!awaitingAuxes) return

        awaitingAuxes = false

        val intent = Intent(this, AuxListActivity::class.java)
        intent.putExtra("auxes", ArrayList(auxes))
        startActivity(intent)
    }

    override fun onDisconnected() = returnToLogin()

    private fun logout() {
        MixerClient.logout(SessionStore.getToken(this))
        SessionStore.clear(this)
        MixerClient.disconnect()
        returnToLogin()
    }

    private fun returnToLogin() {
        val intent = Intent(this, ConnectActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        startActivity(intent)
    }
}
