package com.clmix

/**
 * Mirrors the desktop app's slider taper (ui/main_window.py,
 * AuxLevelsPanel): the fader's dB<->position curve is a piecewise-linear
 * interpolation through LEVEL_TICKS, using LEVEL_TICK_FRACTIONS for each
 * point's position - packed tight near -infinity, spread out near
 * unity, matching a physical fader's printed scale. LevelRulerView draws
 * the same LEVEL_TICKS/LEVEL_TICK_FRACTIONS beside the fader, so a tick
 * mark's printed position always matches exactly where dragging the
 * fader there reports that dB value.
 */
object AuxTaper {
    const val TOP_DB = 10.0
    const val BOTTOM_DB = -150.0

    val LEVEL_TICKS: List<Pair<Double, String>> = listOf(
        BOTTOM_DB to "∞",
        -60.0 to "60",
        -50.0 to "50",
        -40.0 to "40",
        -30.0 to "30",
        -20.0 to "20",
        -10.0 to "10",
        -5.0 to "5",
        0.0 to "0",
        5.0 to "5",
        TOP_DB to "10"
    )

    val LEVEL_TICK_FRACTIONS: List<Double> = cumulativeWeightedFractions(LEVEL_TICKS.size - 1)

    // Fraction 0 (bottom) .. 1 (top) at each of gapCount+1 points, with
    // gap widths growing 1, 2, 3, ... toward the top.
    private fun cumulativeWeightedFractions(gapCount: Int): List<Double> {
        val totalWeight = gapCount * (gapCount + 1) / 2.0
        val fractions = mutableListOf(0.0)

        for (weight in 1..gapCount) {
            fractions.add(fractions.last() + weight / totalWeight)
        }

        return fractions
    }

    fun fractionToDb(fraction: Double): Double {
        val f = fraction.coerceIn(0.0, 1.0)
        val fractions = LEVEL_TICK_FRACTIONS

        for (i in 0 until fractions.size - 1) {
            val f0 = fractions[i]
            val f1 = fractions[i + 1]

            if (f <= f1) {
                val db0 = LEVEL_TICKS[i].first
                val db1 = LEVEL_TICKS[i + 1].first
                val t = (f - f0) / (f1 - f0)
                return db0 + t * (db1 - db0)
            }
        }

        return TOP_DB
    }

    fun dbToFraction(db: Double): Double {
        val ticks = LEVEL_TICKS
        val fractions = LEVEL_TICK_FRACTIONS

        if (db <= ticks.first().first) return 0.0
        if (db >= ticks.last().first) return 1.0

        for (i in 0 until ticks.size - 1) {
            val db0 = ticks[i].first
            val db1 = ticks[i + 1].first

            if (db <= db1) {
                val t = (db - db0) / (db1 - db0)
                return fractions[i] + t * (fractions[i + 1] - fractions[i])
            }
        }

        return 1.0
    }
}
