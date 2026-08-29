package com.clmix

import java.io.Serializable

data class AuxBus(val index: Int, val name: String) : Serializable

data class ChannelState(
    val channel: Int,
    val name: String,
    val level: Double?,
    val pan: Double?,
    // Muted *in the currently selected aux mix* only - the server maps
    // this to the console's per-send on/off flag, not the channel mute
    // that would cut the source for FOH and every other mix too.
    val muted: Boolean
)
