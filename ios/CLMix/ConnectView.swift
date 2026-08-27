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
        Form {
            if !model.discoveredServers.isEmpty {
                Section("Found on this network") {
                    ForEach(model.discoveredServers) { server in
                        Button(server.id) {
                            // Picking a discovered server always folds the
                            // manual section back down if it was open -
                            // the two are alternative ways to pick a
                            // server, not meant to be used together.
                            withAnimation { showManualFields = false }
                            host = server.host
                            port = String(server.port)
                            credentialsEnabled = true
                            selectedServerID = server.id
                        }
                        .foregroundStyle(.white)
                        .listRowBackground(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(selectedServerID == server.id ? Color.accentColor : Color.clear)
                                .overlay(
                                    RoundedRectangle(cornerRadius: 8)
                                        .stroke(
                                            selectedServerID == server.id ? Color.clear : Color.white,
                                            lineWidth: 1
                                        )
                                )
                                .padding(.vertical, 2)
                        )
                    }
                }
            }

            // A toggle, not a one-shot reveal - tapping again folds the
            // fields back and disables Login, matching a discovered-server
            // pick unwinding itself if the user changes their mind.
            Button("Manual") {
                withAnimation { showManualFields.toggle() }
                credentialsEnabled = showManualFields
            }

            if showManualFields {
                Section("Server") {
                    TextField("Host", text: $host)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Port", text: $port)
                        .keyboardType(.numberPad)
                }
            }

            Section("Login") {
                TextField("Username", text: $username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .disabled(!credentialsEnabled)
                SecureField("Password", text: $password)
                    .disabled(!credentialsEnabled)
            }

            if !model.statusMessage.isEmpty {
                Text(model.statusMessage)
                    .foregroundStyle(.secondary)
            }

            Button(model.isConnecting ? "Connecting..." : "Connect") {
                guard let portNumber = Int(port) else { return }
                model.connect(host: host, port: portNumber, username: username, password: password)
            }
            .disabled(
                host.isEmpty || port.isEmpty || username.isEmpty
                    || password.isEmpty || model.isConnecting
            )
        }
        .navigationTitle("CLMix")
        .onAppear { model.startDiscovery() }
        .onDisappear { model.stopDiscovery() }
    }
}
