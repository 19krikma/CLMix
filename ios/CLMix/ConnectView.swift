import SwiftUI

struct ConnectView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var themeStore: ThemeStore

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

    // A scan window: open on arrival and again on every tap of the
    // refresh control, closed thirty seconds later. It is what the
    // spinner reports and what decides whether Manual is ever offered -
    // discovery itself keeps browsing underneath either way, so a server
    // that announces itself late still turns up in the list.
    @State private var scanGeneration = 0
    // True from the first frame rather than waiting for the task below
    // to say so, which would show the refresh control for one frame on
    // a screen that is already scanning.
    @State private var scanning = true

    // Set once a whole scan has gone by with nothing found. It is not
    // the same thing as Manual being on screen - see `showManual` - but
    // it never goes back to false: having once had to fall back to
    // typing an address is a fact about this network, and a user who is
    // told the list is empty should not have to sit through another
    // thirty seconds to be told it again.
    @State private var scanCameBackEmpty = false

    // The id of whichever discovered server was last tapped - only that
    // row fills with the accent color at a time, mirroring Android's
    // ConnectActivity (setRowSelected): every other row stays in its
    // plain, unselected appearance instead of every row being filled.
    @State private var selectedServerID: String?

    private enum Field { case username, password }
    @FocusState private var focusedField: Field?

    /// Whether Manual is on screen at all. Typing an address is the
    /// fallback for a network where announcement does not work, not a
    /// second way of doing what the list above already does - so it
    /// appears only once a scan has come back with nothing, and goes
    /// away again the moment a server does turn up. Derived rather than
    /// latched, so a server appearing, going away and coming back is
    /// handled by the same two facts rather than by three edges.
    ///
    /// The exception is a user already typing into it: a form that
    /// vanishes mid-address because the desktop finally announced
    /// itself is worse than one that stays a moment too long. Picking a
    /// discovered server folds the fields away (see serverRow), which
    /// takes this with it.
    private var showManual: Bool {
        scanCameBackEmpty && (model.discoveredServers.isEmpty || showManualFields)
    }

    /// How long a scan runs before it gives up and offers Manual.
    /// Announcement on a quiet network is usually answered inside a
    /// second or two; this is long enough that a phone which has just
    /// joined the Wi-Fi, or a desktop still starting up, is not written
    /// off, and short enough that somebody standing at the console with
    /// an address in their hand is not left waiting on it.
    private static let scanWindow: TimeInterval = 30

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
            // Over the form rather than in it, so it keeps its corner
            // whatever the form is doing above it.
            .overlay(alignment: .bottomTrailing) { themeToggle }
        }
        // The artwork runs to the very top of the screen, under the
        // status bar/notch, same as Android's enableEdgeToEdge() - the
        // clock and status icons sit over the picture itself rather than
        // over an opaque bar. Only the top edge here: the form still
        // respects the home indicator's safe area at the bottom, same as
        // before, while the background reaches past it on its own.
        .ignoresSafeArea(edges: .top)
        .onAppear {
            model.startDiscovery()
            model.resumeSessionIfPossible()
        }
        .onDisappear { model.stopDiscovery() }
        // Keyed on the generation, so a refresh cancels the window in
        // flight and opens a fresh one rather than letting the first
        // one's deadline close the second. A cancelled run deliberately
        // leaves `scanning` alone: the run replacing it sets it true on
        // its own first line, and the only other way out of here is the
        // view going away.
        .task(id: scanGeneration) {
            scanning = true
            try? await Task.sleep(for: .seconds(Self.scanWindow))
            guard !Task.isCancelled else { return }
            withAnimation {
                scanning = false
                if model.discoveredServers.isEmpty { scanCameBackEmpty = true }
            }
        }
    }

    // Always on screen, like Android's discovered_container - only the
    // row list under the "Discovered" header grows/shrinks as servers
    // come and go on the network, and Manual appears under a rule at the
    // bottom once a scan has gone by without finding anything.
    private var discoveredBox: some View {
        VStack(spacing: 12) {
            Text("Discovered")
                .frame(maxWidth: .infinity)
                .frame(height: 52)
                // Over the title rather than beside it: the title stays
                // centred in the box whether or not anything is showing
                // here, so the header does not shift when a scan ends.
                .overlay(alignment: .trailing) { scanIndicator }

            if !model.discoveredServers.isEmpty {
                VStack(spacing: 8) {
                    ForEach(model.discoveredServers) { server in
                        serverRow(server)
                    }
                }
            }

            if showManual {
                // Out to the box's own edges, against the 12pt padding
                // below: a rule that stopped short of them would read as
                // another control rather than as the box dividing.
                Rectangle()
                    .fill(Color.clmixLoginOutline)
                    .frame(height: 1)
                    .padding(.horizontal, -12)

                manualSection
            }
        }
        .padding(12)
        .overlay(boxBorder)
        // The list arrives from the model rather than from a tap here,
        // so Manual's coming and going has to be animated against the
        // result rather than at the point of change.
        .animation(.easeInOut(duration: 0.2), value: showManual)
    }

    /// Sun or moon in the bottom corner. This screen is the one place
    /// the theme can be set before there is a mixer to set it from -
    /// useful for a phone that has been handed to someone, or for an
    /// operator who wants the light palette up before the lights go
    /// down.
    ///
    /// The whole screen answers the tap now that the wallpaper has a day
    /// cut of its own (see LoginBackgroundView) - palette, status bar
    /// and artwork swap together, where before only this icon changed
    /// and what the tap set was every screen past login.
    private var themeToggle: some View {
        let dark = themeStore.isDarkEffective

        return Button {
            themeStore.isDarkMode = !dark
        } label: {
            Image(systemName: dark ? "moon.fill" : "sun.max.fill")
                .font(.system(size: 18, weight: .medium))
                .frame(width: 48, height: 48)
                // Frosted rather than filled, the same material the
                // wallpaper's own band is made of, so the control reads
                // as sitting on the picture rather than punched into it.
                .background(Circle().fill(.ultraThinMaterial))
                .overlay(Circle().stroke(Color.clmixLoginOutline, lineWidth: 1))
                .contentShape(Circle())
        }
        .foregroundStyle(Color.clmixPrimary)
        .padding(20)
        .animation(.easeInOut(duration: 0.2), value: dark)
        .accessibilityLabel(dark ? "Switch to light mode" : "Switch to dark mode")
    }

    /// The refresh control and the scan spinner share one slot, because
    /// they are the same thing in its two states: while a window is open
    /// the phone is already doing what the button asks for, and a button
    /// that restarts a scan already running is a button that looks
    /// broken. Tapping is what reopens the window, so the spinner
    /// returning is the tap's own feedback.
    @ViewBuilder
    private var scanIndicator: some View {
        if scanning {
            ProgressView()
                .controlSize(.small)
                .tint(Color.clmixPrimary)
                .frame(width: 44, height: 44)
                .accessibilityLabel("Looking for servers")
        } else {
            Button(action: rescan) {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 17, weight: .semibold))
                    .frame(width: 44, height: 44)
            }
            .foregroundStyle(Color.clmixPrimary)
            .accessibilityLabel("Search again")
        }
    }

    /// Throws the list away and browses again from nothing, rather than
    /// leaving what is already there and waiting for more: a stale row
    /// for a desktop that has since gone is the one thing a refresh is
    /// for. The selection goes with it - the row it pointed at is gone -
    /// but the host and port it filled in stay, so a login already set
    /// up is not undone by asking the network again.
    private func rescan() {
        selectedServerID = nil
        model.restartDiscovery()
        scanGeneration += 1
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
                .stroke(selected ? Color.clear : Color.clmixLoginOutline, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // A toggle, not a one-shot reveal - tapping again folds the fields
    // back and disables Login, matching a discovered-server pick
    // unwinding itself if the user changes their mind.
    //
    // No longer a box of its own: it is what the Discovered box falls
    // back on, so it lives inside it, under the rule - see discoveredBox
    // for when it is offered at all.
    private var manualSection: some View {
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
        let border = model.credentialsRejected ? Color.clmixMuteActive : Color.clmixLoginOutline

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
        _ title: String, text: Binding<String>, isSecure: Bool, borderColor: Color = .clmixLoginOutline
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
        RoundedRectangle(cornerRadius: 8).stroke(Color.clmixLoginOutline, lineWidth: 1)
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
