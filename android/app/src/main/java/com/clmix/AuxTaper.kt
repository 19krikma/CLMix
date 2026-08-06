package com.clmix

/**
 * Mirrors the desktop app's slider taper (ui/main_window.py,
 * AuxLevelTestWindow): the fader travels smoothly between TOP_DB and
 * MID_DB, then drops steeply to BOTTOM_DB in the bottom FRACTION_MID
 * of its travel - matching the physical console's fader feel.
 */
object AuxTaper {
    const val TOP_DB = 10.0
    const val MID_DB = -60.0
    const val BOTTOM_DB = -150.0
    const val FRACTION_MID = 0.2

    fun fractionToDb(fraction: Double): Double {
        return if (fraction >= FRACTION_MID) {
            val t = (fraction - FRACTION_MID) / (1 - FRACTION_MID)
            MID_DB + t * (TOP_DB - MID_DB)
        } else {
            val t = fraction / FRACTION_MID
            BOTTOM_DB + t * (MID_DB - BOTTOM_DB)
        }
    }

    fun dbToFraction(db: Double): Double {
        return if (db >= MID_DB) {
            val t = (db - MID_DB) / (TOP_DB - MID_DB)
            FRACTION_MID + t * (1 - FRACTION_MID)
        } else {
            val t = (db - BOTTOM_DB) / (MID_DB - BOTTOM_DB)
            maxOf(0.0, t * FRACTION_MID)
        }
    }
}
