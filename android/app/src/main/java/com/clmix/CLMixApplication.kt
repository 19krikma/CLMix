package com.clmix

import android.app.Application

class CLMixApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        MixerClient.init(this)
    }
}
