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
                        }
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
