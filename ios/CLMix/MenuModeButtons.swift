import SwiftUI

/// Which of the two screens an account holding Mixer Control is on.
enum MixScreen { case channels, aux }

/// The Channels and AUX buttons at the top of both mixer screens' menu,
/// just under the account. Both always shown, Channels above AUX; the one
/// in front is filled in the accent so it reads as where you are, the
/// other grey as the way across. Tapping the one you are on does nothing.
/// Shown only to an account holding Mixer Control - see the callers.
/// Mirrors Android's menu_mode_buttons.xml + bindModeButtons.
struct MenuModeButtons: View {
    let current: MixScreen
    let goAcross: () -> Void

    var body: some View {
        VStack(spacing: 8) {
            button("Channels", for: .channels)
            button("AUX", for: .aux)
        }
        .padding(.horizontal, 14)
        .padding(.top, 12)
    }

    private func button(_ title: String, for screen: MixScreen) -> some View {
        let active = screen == current

        return Button {
            if !active { goAcross() }
        } label: {
            Text(title)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
        }
        .foregroundStyle(active ? Color.clmixOnPrimary : Color.clmixOnMuteInactive)
        .background(active ? Color.clmixPrimary : Color.clmixMuteInactive)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .accessibilityAddTraits(active ? .isSelected : [])
    }
}
