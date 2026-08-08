import Foundation

/// Pan is a plain linear -1.0 (hard left) .. 1.0 (hard right) value with
/// 0.0 as center - unlike level it has no dB-style taper. Mirrors the
/// Android app's PanFormat.kt (minus the SeekBar progress-scale
/// conversions, which SwiftUI's Slider doesn't need - it binds to the
/// -1...1 Double directly).
enum PanFormat {
    // "C" / "L35" / "R20"
    static func shortLabel(_ pan: Double) -> String {
        let amount = Int((abs(pan) * 100).rounded())

        if amount == 0 {
            return "C"
        }

        return pan < 0 ? "L\(amount)" : "R\(amount)"
    }

    static func buttonLabel(_ pan: Double?) -> String {
        guard let pan else { return "Pan" }
        return shortLabel(pan)
    }
}
