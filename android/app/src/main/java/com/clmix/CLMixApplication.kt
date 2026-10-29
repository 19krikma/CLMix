package com.clmix

import android.app.Application
import androidx.appcompat.app.AppCompatDelegate

class CLMixApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        // CLMix is dark at all times, whatever the phone is set to. Before
        // any activity is created, so the first one already inflates dark.
        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
        MixerClient.init(this)
        registerActivityLifecycleCallbacks(ServerSync)
        Sos.init(this)
        registerActivityLifecycleCallbacks(Sos)
    }
}
