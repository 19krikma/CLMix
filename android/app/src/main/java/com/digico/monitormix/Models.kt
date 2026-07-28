package com.digico.monitormix

import java.io.Serializable

data class AuxBus(val index: Int, val name: String) : Serializable

data class ChannelState(
    val channel: Int,
    val name: String,
    val level: Double?,
    val pan: Double?,
    val muted: Boolean
)
