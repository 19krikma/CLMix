import Foundation

/// The shape the EQ screen draws: what the channel's filters and bands do
/// to each frequency, in dB.
///
/// A picture of the EQ, not a model of the console's DSP. Each band is the
/// textbook analogue prototype (RBJ's peaking and shelving forms) evaluated
/// straight at the frequency, and each filter a 12 dB/octave Butterworth -
/// close enough to read where a cut sits and how wide it is, which is what
/// the graph is for. The desk's own filter slopes and Q law have never been
/// measured, so the drawn curve should not be read to a fraction of a dB.
/// Mirrors Android's EqResponse.kt.
enum EqResponse {
    enum Shape { case bell, lowShelf, highShelf }

    // Console band numbers by role. On every channel ever captured band 1
    // sat highest and band 4 lowest, and a freshly reset bus seeds them
    // that way round too (PROTOCOL.md's input and aux EQ tables), so the
    // labels follow it. Only the labels and the shelf choice below lean on
    // this - the graph places every band by its own frequency.
    static let highBand = 1
    static let lowBand = EqRange.bandCount

    /// Low to high, the order the screen lays the bands out in.
    static let bandsLowToHigh = Array((1...EqRange.bandCount).reversed())

    static func label(_ band: Int) -> String {
        switch band {
        case 4: return "LF"
        case 3: return "LMF"
        case 2: return "HMF"
        case 1: return "HF"
        default: return "B\(band)"
        }
    }

    /// eq_curve is an enum over 1...4 and only 1 has ever been seen on a
    /// desk, so 1 is drawn as the bell it plainly is. Anything else on an
    /// outer band is drawn as that band's shelf, and as a bell on a mid
    /// band. A guess about the picture only: the value is never written.
    static func shape(_ band: EqBand) -> Shape {
        guard let curve = band.curve, curve != 1 else { return .bell }
        switch band.band {
        case lowBand: return .lowShelf
        case highBand: return .highShelf
        default: return .bell
        }
    }

    /// One band's own contribution at `hz`, ignoring whether it is on.
    static func bandDb(_ band: EqBand, hz: Double) -> Double {
        guard let f0 = band.freq, let gain = band.gain, gain != 0 else { return 0 }

        let q = min(EqRange.q.upperBound, max(EqRange.q.lowerBound, band.q ?? defaultQ))
        let a = pow(10, gain / 40)
        let w = hz / f0
        let w2 = w * w

        let magnitude: Double
        switch shape(band) {
        case .bell:
            magnitude = hypot(1 - w2, w * a / q) / hypot(1 - w2, w / (a * q))
        // Shelves use a fixed slope rather than the band's Q: a shelf drawn
        // with a bell's narrow Q overshoots into a bump at the corner that
        // nothing on the desk would produce.
        case .lowShelf:
            let k = a.squareRoot() / shelfQ * w
            magnitude = a * hypot(a - w2, k) / hypot(1 - a * w2, k)
        case .highShelf:
            let k = a.squareRoot() / shelfQ * w
            magnitude = a * hypot(1 - a * w2, k) / hypot(a - w2, k)
        }

        return 20 * log10(magnitude)
    }

    /// A 12 dB/octave high-pass (`lo`) or low-pass at `hz`.
    static func filterDb(_ filter: EqFilter, lo: Bool, hz: Double) -> Double {
        guard filter.isIn == true, let fc = filter.freq else { return 0 }

        let w = hz / fc
        let den = hypot(1 - w * w, w / butterworthQ)
        let magnitude = lo ? (w * w) / den : 1 / den

        return 20 * log10(max(magnitude, 1e-10))
    }

    /// The whole channel at `hz`: the filters, plus every band that is on
    /// - provided the EQ itself is in, unless `asIfIn`. The filters sit
    /// outside the EQ's own in/out on the desk, so switching the EQ out
    /// leaves them drawn.
    static func totalDb(_ eq: ChannelEq, hz: Double, asIfIn: Bool = false) -> Double {
        var db = filterDb(eq.loFilter, lo: true, hz: hz)
            + filterDb(eq.hiFilter, lo: false, hz: hz)

        if asIfIn || eq.eqIn != false {
            for band in eq.bands where band.on != false {
                db += bandDb(band, hz: hz)
            }
        }

        return db
    }

    private static let defaultQ = 0.71
    private static let shelfQ = 0.71
    private static let butterworthQ = 0.7071
}

/// "45.0 Hz", "125 Hz", "1.25k", "12.5k" - the way a console prints them.
func formatEqFreq(_ hz: Double?) -> String {
    guard let hz else { return "--" }
    switch hz {
    case ..<100: return String(format: "%.1f Hz", hz)
    case ..<1000: return String(format: "%.0f Hz", hz)
    case ..<10000: return String(format: "%.2fk", hz / 1000)
    default: return String(format: "%.1fk", hz / 1000)
    }
}

func formatEqGain(_ db: Double?) -> String {
    guard let db else { return "--" }
    let rounded = (db * 10).rounded() / 10
    return rounded > 0 ? String(format: "+%.1f dB", rounded) : String(format: "%.1f dB", rounded)
}

func formatEqQ(_ q: Double?) -> String {
    guard let q else { return "--" }
    return String(format: "%.2f", q)
}
