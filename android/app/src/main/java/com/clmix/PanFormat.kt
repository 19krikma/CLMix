package com.clmix

import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Pan is a plain linear -1.0 (hard left) .. 1.0 (hard right) value with
 * 0.0 as center - unlike level it has no dB-style taper, so the
 * progress<->pan conversion is a straight linear scale shared by the
 * per-channel Pan button label and the big pan bottom sheet.
 */
object PanFormat {
    const val SEEK_MAX = 200

    fun toProgress(pan: Double) = (((pan + 1.0) / 2.0) * SEEK_MAX).toInt()

    fun toPan(progress: Int): Double {
        val raw = (progress.toDouble() / SEEK_MAX) * 2.0 - 1.0
        return Math.round(raw * 100.0) / 100.0
    }

    // "C" / "L35" / "R20"
    fun shortLabel(pan: Double): String {
        val amount = (abs(pan) * 100).roundToInt()
        return when {
            amount == 0 -> "C"
            pan < 0 -> "L$amount"
            else -> "R$amount"
        }
    }

    fun buttonLabel(pan: Double?) = if (pan == null) "Pan" else shortLabel(pan)
}
