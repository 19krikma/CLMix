import SwiftUI

/// The connect screen's header: the CLMix feature graphic pinned to the
/// very top, drawn full-bleed at its own aspect ratio, dissolving into
/// whatever ConnectView's background happens to be. Mirrors Android's
/// FadingBannerView.kt.
///
/// The dissolve is done by erasing the drawn content's own alpha
/// (blendMode .destinationOut) rather than by painting a coloured scrim
/// over it, so the graphic melts into the day palette and the night one
/// alike without either being named here - what shows through the erased
/// part is just ConnectView's own background.
///
/// There are two banners, picked by colorScheme - dark artwork at night,
/// a light one by day, same as Android picks between drawable-nodpi and
/// drawable-night-nodpi. Each one's bottom `tailStart` of its height is a
/// single flat colour (black by night, `clmixBackground`'s light value by
/// day - Android samples this off the bitmap itself; fixed here since
/// there are exactly two known assets rather than an arbitrary one), so
/// once the artwork runs out this view keeps filling that colour down to
/// `fadeEnd`: the seam is invisible and the fade can be stretched well
/// past the image itself. That matters because the fade is positioned
/// against the *content* on top of this view (it runs from the
/// Discovered box down to Manual - see ConnectView), not against the
/// artwork's own proportions.
struct FadingBannerView: View {
    @Environment(\.colorScheme) private var colorScheme

    /// Where the dissolve starts and ends, in this view's own points -
    /// both come from ConnectView measuring the Discovered/Manual boxes'
    /// actual on-screen positions, so the fade always finishes exactly
    /// where the form begins regardless of how tall the boxes above it
    /// are (e.g. however many servers Discovered is currently showing).
    let fadeStart: CGFloat
    let fadeEnd: CGFloat

    /// Fraction of the artwork's height above which anything is drawn;
    /// the rest of it is flat paper. ConnectView uses this to drop its
    /// content in just below the logo instead of on top of it.
    static let tailStart: CGFloat = 0.858

    // The assets' own pixel dimensions - only the ratio matters, since
    // this view always draws them scaled to fill its own width. Both
    // banners share it. ConnectView needs this too, to reserve the same
    // amount of space above its content that this view will actually draw
    // into.
    static let aspectRatio: CGFloat = 1024.0 / 500.0

    private var imageName: String {
        colorScheme == .dark ? "clmix_feature_graphic_dark" : "clmix_feature_graphic_light"
    }

    private var tailColor: Color {
        colorScheme == .dark ? .black : .clmixBackground
    }

    var body: some View {
        GeometryReader { geo in
            let width = geo.size.width
            let imageHeight = width / Self.aspectRatio
            let clampedFadeEnd = max(fadeEnd, fadeStart + 1)
            let viewHeight = max(imageHeight, clampedFadeEnd)

            Canvas { context, _ in
                context.draw(
                    Image(imageName),
                    in: CGRect(x: 0, y: 0, width: width, height: imageHeight)
                )

                // Overlaps the artwork's last row by a pixel so no
                // hairline of background shows through at the join.
                if viewHeight > imageHeight {
                    context.fill(
                        Path(CGRect(
                            x: 0, y: imageHeight - 1,
                            width: width, height: viewHeight - imageHeight + 1
                        )),
                        with: .color(tailColor)
                    )
                }

                context.blendMode = .destinationOut
                context.fill(
                    Path(CGRect(x: 0, y: fadeStart, width: width, height: viewHeight - fadeStart)),
                    with: .linearGradient(
                        Self.fadeGradient,
                        startPoint: CGPoint(x: 0, y: fadeStart),
                        endPoint: CGPoint(x: 0, y: clampedFadeEnd)
                    )
                )
            }
            .frame(height: viewHeight)
        }
    }

    // Eased rather than linear: the graphic gives up most of its opacity
    // early on, which keeps the Discovered box's own label - dark-on-light
    // in day mode - readable where it sits over the top of the fade,
    // while the last of the black still doesn't clear until fadeEnd.
    // Mirrors Android's buildFadeShader.
    private static let fadeGradient: Gradient = {
        let steps = 12
        let stops = (0...steps).map { i -> Gradient.Stop in
            let t = CGFloat(i) / CGFloat(steps)
            let erased = 1 - (1 - t) * (1 - t)
            return Gradient.Stop(color: Color.black.opacity(erased), location: t)
        }
        return Gradient(stops: stops)
    }()
}
