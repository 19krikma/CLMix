import SwiftUI

/// The connect screen's wallpaper: the CLMix artwork drawn full-bleed
/// behind the whole form, with a frosted, darkened band down the middle
/// so the boxes, fields and button on top of it stay readable.
///
/// The band is the point of this view. The artwork is a photograph's
/// worth of detail - hanging lights, a brushed logo, a wet floor - and
/// an outlined text field over any of that is unreadable. Rather than
/// boxing the form into an opaque panel, which would hide the picture
/// wherever the form happens to reach, the frost is masked: full
/// strength down the column the controls occupy, easing away towards
/// the screen's edges, where the picture is left as it is. The lights
/// across the top, the strings down the margins and the reflections
/// along the bottom all stay sharp, the logo behind the form reads as a
/// glow rather than as a picture somebody put a form on top of, and
/// nothing on the screen has a hard edge for the eye to catch.
///
/// There is no light variant and the screen does not ask for one - the
/// artwork is black, so ConnectView is pinned to the dark palette in
/// both themes (see CLMixApp). That is what lets the frost be a plain
/// material plus black rather than anything theme-aware.
///
/// Nothing here observes AppModel, deliberately: discovery adds and
/// removes server rows every few seconds while this screen is up, and a
/// blurred full-screen image has no business being re-rendered for that.
struct LoginBackgroundView: View {
    /// Where the form's first box starts, in points from the top of the
    /// screen. The frost fades in just above it, so the strip of
    /// artwork over the form - the hanging lights, the best of the
    /// picture - is left untouched.
    let bandTop: CGFloat

    /// Fraction of the screen's height left clear above the form.
    /// ConnectView pads its content down by this and hands the same
    /// number back as `bandTop`, so the two cannot drift apart.
    ///
    /// Lower than the old banner's give-way point, and deliberately:
    /// with Manual and Demo Mode both showing, the form had grown to
    /// where the Login button sat against the bottom of the screen with
    /// a third of the artwork left empty above it. This is the same
    /// number on both platforms, and the first box lands at exactly it
    /// on each - so it is also the one number to change to move the
    /// whole form up or down.
    static let contentTopFraction: CGFloat = 0.20

    // MARK: - The frost's shape
    //
    // Every number here was set against the artwork itself rather than
    // picked for roundness - see the note on `sideHold` for the one
    // that matters most.

    /// How far above `bandTop` the frost starts coming up, and how far
    /// above it the frost has reached full strength. Both negative: the
    /// ramp finishes just before the first box rather than inside it,
    /// so no control sits half on and half off the band.
    private static let fadeInStart: CGFloat = -130
    private static let fadeInEnd: CGFloat = -10

    /// Fraction of the half-width held at full strength before the
    /// sideways fade begins.
    ///
    /// Deliberately wide. The obvious value is something near half, so
    /// the fade has most of the margin to run in - but the logo in this
    /// artwork is brushed nearly edge to edge, and a fade that clears
    /// that early cuts the bright tips of the C and the x out of the
    /// frost and leaves them hanging crisply beside the boxes. Holding
    /// the band out to here keeps the whole logo under it; what the
    /// margins show instead is the light strings and the reflections,
    /// which is the part of the picture that survives being seen in a
    /// strip 25pt wide.
    private static let sideHold: CGFloat = 0.72

    /// Where the frost starts easing off towards the bottom of the
    /// screen, and how much of it is left at the bottom edge. The
    /// reflections along the floor are the other half of the artwork,
    /// and nothing but the login button - which is an opaque fill, and
    /// needs no help from the band - is ever down there.
    private static let tailStart: CGFloat = 0.82
    private static let tailFloor: CGFloat = 0.30

    /// How dark the band goes, on top of the blur. The material alone
    /// only frosts; this is what makes the picture under the form
    /// recede far enough for a thin outlined box to read against it.
    private static let darkening: CGFloat = 0.48

    /// How strongly the frost's blur comes through, independent of
    /// `darkening`. Material has no blur-radius knob of its own - this
    /// blends between the full `.ultraThinMaterial` blur and the sharp
    /// picture underneath, so the band softens the artwork rather than
    /// smearing it.
    private static let blurStrength: CGFloat = 0.80

    var body: some View {
        GeometryReader { geo in
            // .top so the crop is predictable off the phone portrait
            // ratio the artwork was cut to: landscape, and the iPad's
            // squarer frames, then show the top of the picture (lights
            // over black) rather than whichever slice happened to land
            // in the middle.
            Image("clmix_login_background")
                .resizable()
                .aspectRatio(contentMode: .fill)
                .frame(width: geo.size.width, height: geo.size.height, alignment: .top)
                .clipped()
                .overlay { frost(height: geo.size.height) }
        }
        // Black rather than the themed background: the artwork's own
        // corners are black, so anything the image does not reach - an
        // aspect ratio it was never cut for - reads as more picture.
        .background(Color.black)
    }

    /// The blurred, darkened band itself. A material rather than a
    /// blurred copy of the image: the system's blur is composited on the
    /// GPU off a live snapshot of what is behind it, where a second
    /// `Image(...).blur(...)` would be rasterized on the CPU at full
    /// screen size every time this view was evaluated.
    private func frost(height: CGFloat) -> some View {
        ZStack {
            Rectangle().fill(.ultraThinMaterial)
                .opacity(Self.blurStrength)
            Color.black.opacity(Self.darkening)
        }
        // Two masks multiplied: across, the fade out to the margins;
        // down, the fade in above the form and back out over the floor.
        // Alpha-masking a blur reads as the blur itself weakening - the
        // sharp picture underneath comes through in the same
        // proportion - which is exactly the "clearer towards the edges"
        // this is after, and is why the strength is carried by the mask
        // rather than by a radius.
        .mask {
            LinearGradient(gradient: Self.sideFade, startPoint: .leading, endPoint: .trailing)
                .mask(verticalFade(height: height))
        }
        .allowsHitTesting(false)
    }

    /// Full strength across the middle `sideHold` of the width, then
    /// smoothstepped out to nothing at both edges. Smoothstep rather
    /// than linear because a linear ramp leaves a visible line where it
    /// reaches full strength - the eye finds the corner in a gradient
    /// even when it cannot see the gradient itself.
    private static let sideFade: Gradient = {
        let steps = 24
        let stops = (0...steps).map { i -> Gradient.Stop in
            let t = CGFloat(i) / CGFloat(steps)
            let offCentre = abs(t - 0.5) * 2
            let alpha: CGFloat
            // Spelled out rather than `Self.`: this is a stored static
            // being built, so the type is the only scope in reach.
            let hold = LoginBackgroundView.sideHold
            if offCentre <= hold {
                alpha = 1
            } else {
                alpha = 1 - LoginBackgroundView.smoothstep((offCentre - hold) / (1 - hold))
            }
            return Gradient.Stop(color: .black.opacity(alpha), location: t)
        }
        return Gradient(stops: stops)
    }()

    /// Up above the form, held through it, and off again over the
    /// floor. Built against the view's own height rather than declared
    /// in fractions, because the top ramp is positioned against
    /// `bandTop`, which is a measurement in points.
    private func verticalFade(height: CGFloat) -> LinearGradient {
        let h = max(height, 1)
        let rampStart = (bandTop + Self.fadeInStart) / h
        let rampEnd = (bandTop + Self.fadeInEnd) / h
        let steps = 8

        var stops = (0...steps).map { i -> Gradient.Stop in
            let t = CGFloat(i) / CGFloat(steps)
            let location = rampStart + (rampEnd - rampStart) * t
            return Gradient.Stop(
                color: .black.opacity(Self.smoothstep(t)),
                location: min(max(location, 0), 1)
            )
        }

        stops.append(Gradient.Stop(color: .black, location: Self.tailStart))
        stops += (1...steps).map { i -> Gradient.Stop in
            let t = CGFloat(i) / CGFloat(steps)
            return Gradient.Stop(
                color: .black.opacity(1 - (1 - Self.tailFloor) * Self.smoothstep(t)),
                location: Self.tailStart + (1 - Self.tailStart) * t
            )
        }

        return LinearGradient(stops: stops, startPoint: .top, endPoint: .bottom)
    }

    private static func smoothstep(_ t: CGFloat) -> CGFloat {
        let t = min(max(t, 0), 1)
        return t * t * (3 - 2 * t)
    }
}
