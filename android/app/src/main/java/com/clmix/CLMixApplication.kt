package com.clmix

import android.app.Application

class CLMixApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        // Before any activity is created, so the saved light/dark choice
        // is already in effect when the first one inflates.
        ThemeStore.apply(this)
        MixerClient.init(this)
    }
}
