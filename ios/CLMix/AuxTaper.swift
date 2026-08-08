import Foundation

/// Mirrors the desktop app's slider taper (ui/main_window.py,
/// AuxLevelsPanel) and the Android app's AuxTaper.kt: the fader travels
/// smoothly between topDb and midDb, then drops steeply to bottomDb in
/// the bottom fractionMid of its travel - matching the physical
/// console's fader feel.
enum AuxTaper {
    static let topDb = 10.0
    static let midDb = -60.0
    static let bottomDb = -150.0
    static let fractionMid = 0.2

    static func fractionToDb(_ fraction: Double) -> Double {
        if fraction >= fractionMid {
            let t = (fraction - fractionMid) / (1 - fractionMid)
            return midDb + t * (topDb - midDb)
        }

        let t = fraction / fractionMid
        return bottomDb + t * (midDb - bottomDb)
    }

    static func dbToFraction(_ db: Double) -> Double {
        if db >= midDb {
            let t = (db - midDb) / (topDb - midDb)
            return fractionMid + t * (1 - fractionMid)
        }

        let t = (db - bottomDb) / (midDb - bottomDb)
        return max(0, t * fractionMid)
    }
}
