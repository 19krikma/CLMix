import SwiftUI

/// The chat every logged-in phone shares with the sound engineer: anyone
/// can post, everyone sees it. Opened from the aux mixer's menu. Mirrors
/// Android's ChatActivity.
///
/// Messages are signed with the account's name; the desk's own come in
/// as "Sound Tech" on a bubble of their own colour, and every SOS shows
/// up here too, in red. The history lives in AppModel.chatMessages,
/// which keeps taking messages while this is closed.
struct ChatView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var draft = ""
    @FocusState private var inputFocused: Bool

    // Chat bubbles: everyone else's, this phone's own, and the sound
    // engineer's - the last loud enough to stand out from the rest.
    private static let otherFill = Color(red: 0x2A / 255, green: 0x2D / 255, blue: 0x35 / 255)
    private static let mineFill = Color(red: 0x2E / 255, green: 0x3D / 255, blue: 0x5E / 255)

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                messages
                inputBar
            }
            .background(Color.clmixBackground.ignoresSafeArea())
            .navigationTitle("Chat")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .onAppear {
            model.chatOpen = true
            model.requestChatHistory()
        }
        .onDisappear { model.chatOpen = false }
    }

    private var messages: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 8) {
                    if model.chatMessages.isEmpty {
                        Text("No messages yet. Say hello to everyone, or the sound tech.")
                            .font(.system(size: 14))
                            .foregroundStyle(Color.clmixOnSurfaceVariant)
                            .multilineTextAlignment(.center)
                            .padding(.top, 40)
                            .padding(.horizontal, 24)
                    }
                    ForEach(model.chatMessages) { message in
                        bubble(message).id(message.id)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
            }
            .scrollDismissesKeyboard(.interactively)
            .onAppear { scrollToEnd(proxy, animated: false) }
            .onChange(of: model.chatMessages.last?.id) { _, _ in
                scrollToEnd(proxy, animated: true)
            }
            .onChange(of: inputFocused) { _, focused in
                if focused { scrollToEnd(proxy, animated: true) }
            }
        }
    }

    private func scrollToEnd(_ proxy: ScrollViewProxy, animated: Bool) {
        guard let last = model.chatMessages.last?.id else { return }
        if animated {
            withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(last, anchor: .bottom) }
        } else {
            proxy.scrollTo(last, anchor: .bottom)
        }
    }

    private func bubble(_ message: ChatMessage) -> some View {
        let fill: Color
        let ink: Color

        if message.isSos {
            fill = SosButton.red
            ink = .white
        } else if message.isTech {
            fill = .clmixPrimary
            ink = .clmixOnPrimary
        } else if message.mine {
            fill = Self.mineFill
            ink = .clmixOnSurface
        } else {
            fill = Self.otherFill
            ink = .clmixOnSurface
        }

        return HStack {
            if message.mine { Spacer(minLength: 48) }

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 8) {
                    Text(message.name)
                        .font(.system(size: 13, weight: .bold))
                    Text(message.time, style: .time)
                        .font(.system(size: 12))
                        .opacity(0.7)
                }
                Text(message.text)
                    .font(.system(size: 16))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .foregroundStyle(ink)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(fill)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))

            if !message.mine { Spacer(minLength: 48) }
        }
    }

    private var inputBar: some View {
        HStack(spacing: 8) {
            // One line, so Return sends rather than starting a new one.
            TextField("Message everyone", text: $draft)
                .focused($inputFocused)
                .submitLabel(.send)
                .onSubmit(send)
                .onChange(of: draft) { _, text in
                    if text.count > 500 { draft = String(text.prefix(500)) }
                }
                .font(.system(size: 16))
                .foregroundStyle(Color.clmixOnSurface)
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .background(Color.clmixSurfaceVariant)
                .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))

            Button(action: send) {
                Text("Send")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(Color.clmixOnPrimary)
                    .padding(.horizontal, 18)
                    .frame(height: 46)
                    .background(Color.clmixPrimary)
                    .clipShape(Capsule())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color.clmixSurface.ignoresSafeArea(edges: .bottom))
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        model.sendChat(text: text)
        draft = ""
    }
}
