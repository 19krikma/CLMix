import SwiftUI

/// The Feedback form, opened from the side menu: a category, a message
/// and an opt-in for diagnostics. Sending goes through FeedbackQueue,
/// which keeps the message on the phone until it has gone - so this
/// works the same with or without the mixer server, and with or without
/// internet. Mirrors Android's FeedbackActivity.
@MainActor
struct FeedbackView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var category: String?
    @State private var message = ""
    @State private var includeDiagnostics = false
    @State private var diagnostics = ""
    @State private var sending = false
    @State private var status: String?
    @State private var statusIsError = false

    private var trimmed: String { message.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var canSend: Bool {
        !sending && category != nil && (Feedback.minLength...Feedback.maxLength).contains(trimmed.count)
    }

    private var connected: Bool { model.screen != .connect }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Report a bug or ask for a feature. Feedback is anonymous: your name and account are not sent. If you're offline, it's kept on this device and sent once you're back online.")
                        .font(.subheadline)
                        .foregroundStyle(Color.clmixOnSurfaceVariant)
                        .listRowBackground(Color.clear)
                }

                Section("Category") {
                    Picker("Category", selection: $category) {
                        Text("Choose…").tag(String?.none)
                        ForEach(Feedback.categories, id: \.self) { name in
                            Text(name).tag(String?.some(name))
                        }
                    }
                }

                Section {
                    TextEditor(text: $message)
                        .frame(minHeight: 140)
                } header: {
                    Text("Message")
                } footer: {
                    HStack {
                        if (1..<Feedback.minLength).contains(trimmed.count) {
                            Text("At least \(Feedback.minLength) characters")
                        }
                        Spacer()
                        Text("\(message.count)/\(Feedback.maxLength)")
                    }
                }

                Section {
                    Toggle("Include diagnostics", isOn: $includeDiagnostics)
                } footer: {
                    // Exactly what the switch adds, so nothing goes that
                    // the user hasn't seen.
                    if includeDiagnostics {
                        Text(diagnostics)
                            .font(.system(.caption, design: .monospaced))
                    }
                }

                if let status {
                    Section {
                        Text(status)
                            .foregroundStyle(statusIsError ? Color.red : Color.clmixOnSurfaceVariant)
                    }
                }

                Section {
                    Button {
                        send()
                    } label: {
                        Text(sending ? "Sending…" : "Send")
                            .bold()
                            .frame(maxWidth: .infinity)
                    }
                    .disabled(!canSend)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Color.clmixBackground.ignoresSafeArea())
            .navigationTitle("Feedback")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .onChange(of: message) { _, new in
            if new.count > Feedback.maxLength {
                message = String(new.prefix(Feedback.maxLength))
            }
        }
        .onChange(of: includeDiagnostics) { _, on in
            if on { diagnostics = Feedback.diagnostics(connected: connected) }
        }
    }

    private func send() {
        guard let category else { return }
        let text = trimmed
        let diag = includeDiagnostics ? Feedback.diagnostics(connected: connected) : nil
        sending = true
        show("Sending…", error: false)

        Task {
            let outcome = await FeedbackQueue.shared.submit(category: category, message: text, diagnostics: diag)
            sending = false
            switch outcome {
            case .sent:
                show("Thank you for your feedback.", error: false)
                message = ""
                includeDiagnostics = false
            case .queued:
                show("Feedback saved and will be submitted automatically when internet becomes available.",
                     error: false)
                message = ""
                includeDiagnostics = false
            case .rejected:
                show("Couldn't send that feedback. Please check it and try again.", error: true)
            }
        }
    }

    private func show(_ text: String, error: Bool) {
        status = text
        statusIsError = error
    }
}
