import Foundation

/// Mirrors the desktop app's slider taper (ui/main_window.py,
/// AuxLevelsPanel) and the Android app's AuxTaper.kt: the fader's
/// dB<->position curve is a piecewise-linear interpolation through
/// levelTicks, using levelTickFractions for each point's position -
/// packed tight near -infinity, spread out near unity, matching a
/// physical fader's printed scale. LevelRulerView draws the same
/// levelTicks/levelTickFractions beside the fader, so a tick mark's
/// printed position always matches exactly where dragging the fader
/// there reports that dB value.
enum AuxTaper {
    static let topDb = 10.0
    static let bottomDb = -150.0

    static let levelTicks: [(db: Double, label: String)] = [
        (bottomDb, "∞"),
        (-60.0, "60"),
        (-50.0, "50"),
        (-40.0, "40"),
        (-30.0, "30"),
        (-20.0, "20"),
        (-10.0, "10"),
        (-5.0, "5"),
        (0.0, "0"),
        (5.0, "5"),
        (topDb, "10"),
    ]

    static let levelTickFractions: [Double] = cumulativeWeightedFractions(levelTicks.count - 1)

    // Fraction 0 (bottom) .. 1 (top) at each of gapCount+1 points, with
    // gap widths growing 1, 2, 3, ... toward the top.
    private static func cumulativeWeightedFractions(_ gapCount: Int) -> [Double] {
        let totalWeight = Double(gapCount * (gapCount + 1)) / 2
        var fractions = [0.0]

        for weight in 1...gapCount {
            fractions.append(fractions[fractions.count - 1] + Double(weight) / totalWeight)
        }

        return fractions
    }

    static func fractionToDb(_ fraction: Double) -> Double {
        let f = min(1, max(0, fraction))
        let fractions = levelTickFractions

        for i in 0..<(fractions.count - 1) {
            let f0 = fractions[i]
            let f1 = fractions[i + 1]

            if f <= f1 {
                let db0 = levelTicks[i].db
                let db1 = levelTicks[i + 1].db
                let t = (f - f0) / (f1 - f0)
                return db0 + t * (db1 - db0)
            }
        }

        return topDb
    }

    static func dbToFraction(_ db: Double) -> Double {
        let ticks = levelTicks
        let fractions = levelTickFractions

        if db <= ticks.first!.db { return 0 }
        if db >= ticks.last!.db { return 1 }

        for i in 0..<(ticks.count - 1) {
            let db0 = ticks[i].db
            let db1 = ticks[i + 1].db

            if db <= db1 {
                let t = (db - db0) / (db1 - db0)
                return fractions[i] + t * (fractions[i + 1] - fractions[i])
            }
        }

        return 1
    }
}
