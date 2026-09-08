import SwiftUI

/// Reports the Discovered/Manual boxes' top-edge Y positions (in the
/// "connectBanner" coordinate space) up to ConnectView, so it can tell
/// FadingBannerView exactly where its fade should start and end -
/// mirrors Android's ConnectActivity reading
/// discoveredContainer.top/manualContainer.top directly off the views.
private struct BoxTopPreferenceKey: PreferenceKey {
    static var defaultValue: [String: CGFloat] = [:]

    static func reduce(value: inout [String: CGFloat], nextValue: () -> [String: CGFloat]) {
        value.merge(nextValue()) { _, new in new }
    }
}

struct ConnectView: View {
    @EnvironmentObject var model: AppModel

    @AppStorage("clmix.host") private var host = ""
    @AppStorage("clmix.port") private var port = "8765"
    @AppStorage("clmix.username") private var username = ""
    @State private var password = ""

    // Both start closed/disabled - there's nothing to type credentials
    // for until a server has actually been picked, either by expanding
    // "Manual" (which reveals the host/port fields) or tapping a
    // discovered server row (which fills them in directly, so they stay
    // hidden).
    @State private var showManualFields = false
    @State private var credentialsEnabled = false

    // The id of whichever discovered server was last tapped - only that
    // row fills with the accent color at a time, mirroring Android's
    // ConnectActivity (setRowSelected): every other row stays in its
    // plain, unselected appearance instead of every row being filled.
    @State private var selectedServerID: String?

    // Where the banner's fade should run, in the ScrollView's own content
    // coordinates - kept live by the Discovered/Manual boxes' own
    // preference reports below, so the fade always finishes exactly at
    // Manual's top regardless of how many servers Discovered is showing.
    @State private var discoveredTop: CGFloat = 0
    @State private var manualTop: CGFloat = 1

    private enum Field { case username, password }
    @FocusState private var focusedField: Field?

    var body: some View {
        GeometryReader { outerGeo in
            // The banner replaced the old "CLMix" / "Remote Aux Control"
            // title block entirely - the form now starts right where the
            // artwork's solid-black tail begins, same as Android's
            // bannerOffsetFor.
            let contentTopPadding = outerGeo.size.width
                / FadingBannerView.aspectRatio * FadingBannerView.tailStart

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    discoveredBox
                        .background(topReader("discovered"))
                        .padding(.bottom, 18)

                    manualBox
                        .background(topReader("manual"))
                        .padding(.bottom, 18)

                    demoBox
                        .padding(.bottom, 18)

                    // Sits immediately above the credentials it refers to,
                    // rather than below the button - mirrors Android's
                    // message_label moving there in ConnectActivity.
                    if !model.statusMessage.isEmpty {
                        Text(model.statusMessage)
                            .font(.system(size: 14))
                            .foregroundStyle(model.statusIsError ? Color.clmixMuteActive : Color.secondary)
                            .padding(.bottom, 12)
                    }

                    credentialField(
                        "Username", text: $username, isSecure: false, field: .username
                    )
                    .padding(.bottom, 14)
                    credentialField(
                        "Password", text: $password, isSecure: true, field: .password
                    )
                    .padding(.bottom, 24)

                    loginButton
                }
                .padding(.horizontal, 28)
                .padding(.top, contentTopPadding)
                .padding(.bottom, 28)
                .frame(maxWidth: .infinity)
                .background(alignment: .top) {
                    FadingBannerView(fadeStart: discoveredTop, fadeEnd: manualTop)
                }
            }
            .coordinateSpace(name: "connectBanner")
            .onPreferenceChange(BoxTopPreferenceKey.self) { tops in
                if let top = tops["discovered"] { discoveredTop = top }
                if let top = tops["manual"] { manualTop = top }
            }
        }
        .background(Color.clmixBackground)
        // The banner runs to the very top of the screen, under the status
        // bar/notch, same as Android's enableEdgeToEdge() - the clock and
        // status icons sit over the artwork itself rather than over an
        // opaque bar. Only the top edge: the bottom still respects the
        // home indicator's safe area, same as before. Unlike Android,
        // there's no separate call needed to pick light-vs-dark status
        // bar content here - iOS already switches that automatically with
        // the color scheme, which is also what picks which banner (dark
        // artwork at night, light by day) is showing.
        .ignoresSafeArea(edges: .top)
        .onAppear {
            model.startDiscovery()
            model.resumeSessionIfPossible()
        }
        .onDisappear { model.stopDiscovery() }
    }

    private func topReader(_ key: String) -> some View {
        GeometryReader { proxy in
            Color.clear.preference(
                key: BoxTopPreferenceKey.self,
                value: [key: proxy.frame(in: .named("connectBanner")).minY]
            )
        }
    }

    // Always on screen, like Android's discovered_container - only the
    // row list under the "Discovered" header grows/shrinks as servers
    // come and go on the network.
    private var discoveredBox: some View {
        VStack(spacing: 12) {
            Text("Discovered")
                .frame(maxWidth: .infinity)
                .frame(height: 52)

            if !model.discoveredServers.isEmpty {
                VStack(spacing: 8) {
                    ForEach(model.discoveredServers) { server in
                        serverRow(server)
                    }
                }
            }
        }
        .padding(12)
        .overlay(boxBorder)
    }

    private func serverRow(_ server: DiscoveredServer) -> some View {
        let selected = selectedServerID == server.id

        return Button {
            // Picking a discovered server always folds the manual section
            // back down if it was open - the two are alternative ways to
            // pick a server, not meant to be used together.
            withAnimation { showManualFields = false }
            host = server.host
            port = String(server.port)
            credentialsEnabled = true
            selectedServerID = server.id
        } label: {
            Text(server.id)
                .frame(maxWidth: .infinity)
                .frame(height: 44)
        }
        // Selected rows fill with the accent color, so their label needs
        // on_primary rather than plain white - light mode's on_primary
        // *is* white, but dark mode's is a dark navy, matching the accent
        // fill's own light/dark swap.
        .foregroundStyle(selected ? Color.clmixOnPrimary : Color.primary)
        .background(selected ? Color.clmixPrimary : Color.clear)
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(selected ? Color.clear : Color.clmixOutline, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // A toggle, not a one-shot reveal - tapping again folds the fields
    // back and disables Login, matching a discovered-server pick
    // unwinding itself if the user changes their mind.
    private var manualBox: some View {
        VStack(spacing: 12) {
            Button {
                withAnimation { showManualFields.toggle() }
                credentialsEnabled = showManualFields
            } label: {
                Text("Manual")
                    .frame(maxWidth: .infinity)
                    .frame(height: 52)
            }
            .foregroundStyle(Color.primary)

            if showManualFields {
                VStack(spacing: 14) {
                    outlinedField("Server Address", text: $host, isSecure: false)
                        .keyboardType(.URL)
                    outlinedField("Port", text: $port, isSecure: false)
                        .keyboardType(.numberPad)
                }
            }
        }
        .padding(12)
        .overlay(boxBorder)
    }

    // Deliberately needs no server, no credentials and no network: App
    // Review has no desktop app or console to connect to, and a demo they
    // cannot reach is what got the first submission rejected under
    // Guideline 2.1. Sits below Manual so it reads as the third way in,
    // after "pick a discovered server" and "type one in".
    //
    // REMOVE WITH DEMO MODE.
    private var demoBox: some View {
        Button {
            model.enterDemoMode()
        } label: {
            VStack(spacing: 4) {
                Text("Demo Mode")
                    .font(.system(size: 16, weight: .semibold))
                Text("Explore CLMix without a mixer server")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.clmixOnSurfaceVariant)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
        }
        .foregroundStyle(Color.primary)
        .overlay(boxBorder)
        .disabled(model.isConnecting)
    }

    // Rejected credentials tint both fields together, the one thing
    // typing into either of them clears - mirrors Android's
    // ConnectActivity marking username_layout/password_layout in red and
    // clearing on the first keystroke that follows.
    @ViewBuilder
    private func credentialField(
        _ title: String, text: Binding<String>, isSecure: Bool, field: Field
    ) -> some View {
        let border = model.credentialsRejected ? Color.clmixMuteActive : Color.clmixOutline

        // The focus binding has to sit on the field itself rather than on
        // anything wrapping it, which is why this doesn't go through
        // outlinedField the way the host/port fields do.
        Group {
            if isSecure {
                SecureField(title, text: text)
                    .focused($focusedField, equals: field)
                    // Enter on the password field logs in; on username it
                    // moves to password. Both routed through the same
                    // guard as the button - see submit().
                    .submitLabel(.go)
                    .onSubmit(submit)
            } else {
                TextField(title, text: text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focusedField, equals: field)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .password }
            }
        }
        .fieldBox(borderColor: border)
        .disabled(!credentialsEnabled)
        .onChange(of: text.wrappedValue) { _, _ in model.clearError() }
    }

    @ViewBuilder
    private func outlinedField(
        _ title: String, text: Binding<String>, isSecure: Bool, borderColor: Color = .clmixOutline
    ) -> some View {
        Group {
            if isSecure {
                SecureField(title, text: text)
            } else {
                TextField(title, text: text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }
        }
        .fieldBox(borderColor: borderColor)
    }

    private var boxBorder: some View {
        RoundedRectangle(cornerRadius: 8).stroke(Color.clmixOutline, lineWidth: 1)
    }

    // The spinner replaces the label while a login is in flight; a
    // failure ("Can't Reach Server", "No Snapshot Access") takes over the
    // label and tints the button red for a few seconds, then both revert
    // to plain "Login" - mirrors Android's connect_button/connect_label/
    // connect_progress trio.
    // Everything a login attempt needs, in one place: the button and
    // Enter on the password field both go through it, so there is one
    // login path rather than two that could drift - in particular a
    // second attempt cannot be started while one is already in flight.
    // Mirrors Android's ConnectActivity routing its editor action through
    // the same guard and the same attemptConnect() as the button.
    private var canSubmit: Bool {
        !host.isEmpty && !port.isEmpty && !username.isEmpty
            && !password.isEmpty && !model.isConnecting
    }

    private func submit() {
        guard canSubmit, let portNumber = Int(port) else { return }
        focusedField = nil
        model.connect(host: host, port: portNumber, username: username, password: password)
    }

    private var loginButton: some View {
        Button(action: submit) {
            Group {
                if model.isConnecting {
                    ProgressView()
                        .tint(Color.clmixOnPrimary)
                } else {
                    Text(model.buttonResultLabel ?? "Login")
                        .fontWeight(.semibold)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 52)
        }
        .foregroundStyle(Color.clmixOnPrimary)
        .background(model.buttonResultIsRejection ? Color.clmixMuteActive : Color.clmixPrimary)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .animation(.easeInOut(duration: 0.25), value: model.buttonResultIsRejection)
        .disabled(!canSubmit)
    }
}

private extension View {
    /// The connect screen's outlined text-field box - shared by the
    /// host/port fields and the credential ones, which build their own
    /// TextField/SecureField so a @FocusState binding can sit on it.
    func fieldBox(borderColor: Color) -> some View {
        self
            .padding(.horizontal, 14)
            .frame(height: 52)
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(borderColor, lineWidth: 1))
    }
}
