import PhotosUI
import SwiftUI
import UIKit

/// An account's picture in a circle, or a person figure when it has
/// none. Used by the menu header and ProfileView.
struct ProfilePicture: View {
    let picture: Data?
    let size: CGFloat
    /// The SF Symbol shown when there is no picture.
    var placeholder = "person.fill"

    var body: some View {
        Group {
            if let picture, let image = UIImage(data: picture) {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                ZStack {
                    Color.clmixMuteInactive
                    Image(systemName: placeholder)
                        .resizable()
                        .scaledToFit()
                        .padding(size * 0.22)
                        .foregroundStyle(Color.clmixOnMuteInactive)
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .accessibilityLabel("Profile picture")
    }
}

/// The top of the side menu: this account's picture and name. Tapping
/// it opens ProfileView. Hidden against a desktop too old to have
/// profiles. Mirrors Android's ProfileHeader.
struct ProfileHeader: View {
    @EnvironmentObject private var model: AppModel
    let onOpen: () -> Void

    private static let divider = Color(red: 0x3A / 255, green: 0x3D / 255, blue: 0x45 / 255)

    var body: some View {
        if let profile = model.profile {
            Button(action: onOpen) {
                HStack(spacing: 14) {
                    ProfilePicture(picture: profile.picture, size: 56)
                    Text(profile.displayName)
                        .font(.system(size: 18, weight: .bold))
                        .foregroundStyle(Color.clmixOnSurface)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 14)
                .padding(.top, 20)
                .padding(.bottom, 16)
                .frame(maxWidth: .infinity)
                // A slightly lighter panel with a line under it, so the
                // account reads apart from the menu's buttons.
                .background(Color.clmixSurfaceVariant)
                .overlay(alignment: .bottom) {
                    Rectangle()
                        .fill(Self.divider)
                        .frame(height: 1)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }
}

/// This account's own profile: picture, first and last name, username
/// and password, all editable from the phone. Changing the username or
/// password asks for the current password too, which the desktop checks
/// (RemoteServer._handle_profile). Mirrors Android's ProfileActivity.
struct ProfileView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var firstName = ""
    @State private var lastName = ""
    @State private var username = ""
    @State private var newPassword = ""
    @State private var confirmPassword = ""
    @State private var currentPassword = ""
    @State private var filled = false

    @State private var photoItem: PhotosPickerItem?
    @State private var confirmRemove = false
    @State private var saving = false
    @State private var status: String?
    @State private var statusIsError = false

    private var profile: AccountProfile? { model.profile }

    private var usernameChanged: Bool {
        guard let profile else { return false }
        return username.trimmingCharacters(in: .whitespaces) != profile.username
    }

    private var passwordChanged: Bool { !newPassword.isEmpty }

    private var hasChanges: Bool {
        guard let profile else { return false }
        return firstName.trimmingCharacters(in: .whitespaces) != profile.firstName
            || lastName.trimmingCharacters(in: .whitespaces) != profile.lastName
            || usernameChanged || passwordChanged
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(spacing: 10) {
                        PhotosPicker(selection: $photoItem, matching: .images) {
                            ProfilePicture(picture: profile?.picture, size: 128)
                        }
                        HStack(spacing: 24) {
                            PhotosPicker("Change photo", selection: $photoItem, matching: .images)
                            if profile?.picture != nil {
                                Button("Remove", role: .destructive) { confirmRemove = true }
                            }
                        }
                        .buttonStyle(.borderless)
                    }
                    .frame(maxWidth: .infinity)
                    .listRowBackground(Color.clear)
                }

                Section("Name") {
                    TextField("First name", text: $firstName)
                        .textContentType(.givenName)
                    TextField("Last name", text: $lastName)
                        .textContentType(.familyName)
                }

                Section {
                    TextField("Username", text: $username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    SecureField("New password", text: $newPassword)
                        .textContentType(.newPassword)
                    if passwordChanged {
                        SecureField("Confirm new password", text: $confirmPassword)
                            .textContentType(.newPassword)
                    }
                    if usernameChanged || passwordChanged {
                        SecureField("Current password", text: $currentPassword)
                            .textContentType(.password)
                    }
                } header: {
                    Text("Account")
                } footer: {
                    Text(usernameChanged || passwordChanged
                         ? "Your current password is needed to change your username or password."
                         : "Leave the new password empty to keep your current one.")
                }

                if let status {
                    Section {
                        Text(status)
                            .foregroundStyle(statusIsError ? Color.red : Color.clmixOnSurfaceVariant)
                    }
                }

                Section {
                    Button {
                        save()
                    } label: {
                        Text(saving ? "Saving…" : "Save")
                            .bold()
                            .frame(maxWidth: .infinity)
                    }
                    .disabled(saving || !hasChanges)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Color.clmixBackground.ignoresSafeArea())
            .navigationTitle("Profile")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .confirmationDialog("Remove photo?", isPresented: $confirmRemove,
                                titleVisibility: .visible) {
                Button("Remove", role: .destructive) {
                    model.setProfilePicture(nil) { ok, message in
                        if !ok { show(message ?? "Couldn't remove the photo", error: true) }
                    }
                }
            }
        }
        .onAppear {
            fill()
            model.requestProfile()
        }
        .onChange(of: model.profile) { old, new in
            // Refilled only where the server's value changed, so a
            // picture arriving doesn't wipe a half-typed name.
            guard let new else { return }
            if !filled { fill(); return }
            if old?.firstName != new.firstName { firstName = new.firstName }
            if old?.lastName != new.lastName { lastName = new.lastName }
            if old?.username != new.username { username = new.username }
        }
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            photoItem = nil
            upload(item)
        }
        .onChange(of: model.screen) { _, screen in
            if screen == .connect { dismiss() }
        }
    }

    private func fill() {
        guard let profile, !filled else { return }
        firstName = profile.firstName
        lastName = profile.lastName
        username = profile.username
        filled = true
    }

    private func show(_ text: String, error: Bool) {
        status = text
        statusIsError = error
    }

    private func save() {
        guard let profile else { return }
        let name = username.trimmingCharacters(in: .whitespaces)

        if name.isEmpty {
            return show("Username can't be empty", error: true)
        }
        if passwordChanged && newPassword != confirmPassword {
            return show("Passwords don't match", error: true)
        }
        if (usernameChanged || passwordChanged) && currentPassword.isEmpty {
            return show("Enter your current password", error: true)
        }

        saving = true
        show("Saving…", error: false)
        model.updateProfile(
            firstName: firstName.trimmingCharacters(in: .whitespaces),
            lastName: lastName.trimmingCharacters(in: .whitespaces),
            username: name == profile.username ? nil : name,
            newPassword: passwordChanged ? newPassword : nil,
            currentPassword: currentPassword.isEmpty ? nil : currentPassword
        ) { ok, message in
            saving = false
            if ok {
                newPassword = ""
                confirmPassword = ""
                currentPassword = ""
                show("Profile saved", error: false)
            } else {
                show(message ?? "Couldn't save the profile", error: true)
            }
        }
    }

    private func upload(_ item: PhotosPickerItem) {
        show("Uploading photo…", error: false)
        Task {
            guard let data = try? await item.loadTransferable(type: Data.self),
                  let encoded = Self.encodePicture(data) else {
                show("Couldn't read that photo", error: true)
                return
            }
            model.setProfilePicture(encoded) { ok, message in
                if ok {
                    status = nil
                } else {
                    show(message ?? "Couldn't upload the photo", error: true)
                }
            }
        }
    }

    /// A photo as the server wants it: cropped to a centred square,
    /// shrunk to 320px and JPEG'd, then base64. UIImage applies the
    /// photo's own orientation while drawing.
    static func encodePicture(_ data: Data) -> String? {
        guard let image = UIImage(data: data) else { return nil }
        let side = min(image.size.width, image.size.height)
        guard side > 0 else { return nil }

        let target: CGFloat = min(320, side)
        let scale = target / side
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: target, height: target), format: format)
        let square = renderer.image { _ in
            let width = image.size.width * scale
            let height = image.size.height * scale
            image.draw(in: CGRect(x: (target - width) / 2, y: (target - height) / 2,
                                  width: width, height: height))
        }
        return square.jpegData(compressionQuality: 0.85)?.base64EncodedString()
    }
}
