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

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text("CLMix")
                    .font(.system(size: 28, weight: .bold))
                    .padding(.bottom, 4)
                Text("Remote Aux Control")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
                    .padding(.bottom, 28)

                discoveredBox
                    .padding(.bottom, 18)

                manualBox
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

                credentialField("Username", text: $username, isSecure: false)
                    .padding(.bottom, 14)
                credentialField("Password", text: $password, isSecure: true)
                    .padding(.bottom, 24)

                loginButton
            }
            .padding(28)
            .frame(maxWidth: .infinity)
        }
        .background(Color.clmixBackground)
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

    // Rejected credentials tint both fields together, the one thing
    // typing into either of them clears - mirrors Android's
    // ConnectActivity marking username_layout/password_layout in red and
    // clearing on the first keystroke that follows.
    private func credentialField(_ title: String, text: Binding<String>, isSecure: Bool) -> some View {
        outlinedField(
            title, text: text, isSecure: isSecure,
            borderColor: model.credentialsRejected ? Color.clmixMuteActive : Color.clmixOutline
        )
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
        .padding(.horizontal, 14)
        .frame(height: 52)
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(borderColor, lineWidth: 1))
    }

    private var boxBorder: some View {
        RoundedRectangle(cornerRadius: 8).stroke(Color.clmixOutline, lineWidth: 1)
    }

    // The spinner replaces the label while a login is in flight; a
    // failure ("Can't Reach Server", "No Snapshot Access") takes over the
    // label and tints the button red for a few seconds, then both revert
    // to plain "Login" - mirrors Android's connect_button/connect_label/
    // connect_progress trio.
    private var loginButton: some View {
        Button {
            guard let portNumber = Int(port) else { return }
            model.connect(host: host, port: portNumber, username: username, password: password)
        } label: {
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
        .disabled(
            host.isEmpty || port.isEmpty || username.isEmpty
                || password.isEmpty || model.isConnecting
        )
    }
}
