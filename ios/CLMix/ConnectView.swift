import SwiftUI

struct ConnectView: View {
    @EnvironmentObject var model: AppModel

    @AppStorage("clmix.host") private var host = ""
    @AppStorage("clmix.port") private var port = "8765"
    @AppStorage("clmix.username") private var username = ""
    @State private var password = ""

    var body: some View {
        Form {
            Section("Server") {
                TextField("Host", text: $host)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("Port", text: $port)
                    .keyboardType(.numberPad)
            }

            Section("Login") {
                TextField("Username", text: $username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Password", text: $password)
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
    }
}
