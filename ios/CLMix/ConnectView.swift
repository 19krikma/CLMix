import SwiftUI

struct ConnectView: View {
    @EnvironmentObject var model: AppModel

    @AppStorage("clmix.host") private var host = ""
    @AppStorage("clmix.port") private var port = "8765"
    @AppStorage("clmix.username") private var username = ""
    @State private var password = ""

    // Both start closed/disabled - there's nothing to type credentials
    // for until a server has actually been picked, either by tapping
    // "Manual" (which also reveals the host/port fields) or a discovered
    // server row (which fills them in directly, so they stay hidden).
    @State private var showManualFields = false
    @State private var credentialsEnabled = false

    var body: some View {
        Form {
            if !model.discoveredServers.isEmpty {
                Section("Found on this network") {
                    ForEach(model.discoveredServers) { server in
                        Button(server.id) {
                            host = server.host
                            port = String(server.port)
                            credentialsEnabled = true
                        }
                    }
                }
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
            } else {
                Button("Manual") {
                    showManualFields = true
                    credentialsEnabled = true
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
        .onAppear {
            model.startDiscovery()

            // Returning user with a remembered address - skip straight
            // past "Manual" instead of making them re-tap it every launch.
            if !host.isEmpty {
                showManualFields = true
                credentialsEnabled = true
            }
        }
        .onDisappear { model.stopDiscovery() }
    }
}
