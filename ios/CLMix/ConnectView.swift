import SwiftUI

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

    private enum Field { case username, password }
    @FocusState private var focusedField: Field?

    var body: some View {
        GeometryReader { outerGeo in
            // The artwork fills the screen rather than heading it, so
            // what this reserves is not room for a picture above the
            // form but the strip of picture the form deliberately stays
            // off: the hanging lights across the top. The same number
            // goes to the background, which starts its frost just above
            // it - see LoginBackgroundView.
            let contentTopPadding = outerGeo.size.height
                * LoginBackgroundView.contentTopFraction

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    discoveredBox
                        .padding(.bottom, 18)

                    manualBox
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
            }
            // Behind the ScrollView rather than behind its content, so
            // the frosted band stays put while the form scrolls through
            // it - it marks where the controls are on screen, not where
            // they are in the scrolling content.
            .background {
                LoginBackgroundView(bandTop: contentTopPadding)
                    .ignoresSafeArea()
            }
        }
        // The artwork runs to the very top of the screen, under the
        // status bar/notch, same as Android's enableEdgeToEdge() - the
        // clock and status icons sit over the picture itself rather than
        // over an opaque bar. Only the top edge here: the form still
        // respects the home indicator's safe area at the bottom, same as
        // before, while the background reaches past it on its own.
        .ignoresSafeArea(edges: .top)
        // The palette is pinned to its night values whatever the phone
        // or the user's own theme choice says, because the artwork
        // behind it is black and there is no light cut of it: in day
        // mode `Color.primary` would resolve to near-black and every
        // label on this screen would disappear into the picture. Only
        // this screen - everything past login follows the theme as it
        // always has. CLMixApp pins the window to match, which is what
        // turns the status bar's own clock and icons white over the top
        // of the artwork; this line is what the colours in here actually
        // read, so the two are deliberately both present.
        .environment(\.colorScheme, .dark)
        .onAppear {
            model.startDiscovery()
            model.resumeSessionIfPossible()
        }
        .onDisappear { model.stopDiscovery() }
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
