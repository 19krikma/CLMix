import Foundation
import Security

/// Holds only the server-issued session token (see RemoteServer._sessions
/// in services/remote_server.py) - never the account password, which this
/// app never writes to disk at all. Mirrors Android's SessionStore.kt,
/// with the Keychain standing in for EncryptedSharedPreferences: the token
/// is a bearer credential, so anyone who can read it can act as this user
/// until it expires (SESSION_TTL_SECONDS), the desktop app restarts, or
/// the user explicitly logs out.
///
/// `kSecAttrAccessibleAfterFirstUnlock` rather than `WhenUnlocked` so a
/// resume can still read the token when the app is relaunched into the
/// background (e.g. the reconnect path) on a device that hasn't been
/// unlocked since - it still keeps the token unreadable while the device
/// has never been unlocked after a boot.
enum SessionStore {
    private static let service = "com.clmix.session"
    private static let account = "token"

    private static var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    static func token() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)

        guard status == errSecSuccess,
              let data = item as? Data,
              let token = String(data: data, encoding: .utf8),
              !token.isEmpty else {
            return nil
        }

        return token
    }

    static func save(_ token: String) {
        guard let data = token.data(using: .utf8) else { return }

        // Delete-then-add rather than SecItemUpdate: this runs on every
        // login (fresh or resumed), and an add over an existing item
        // fails with errSecDuplicateItem, so the delete makes the write
        // idempotent without a separate "does it exist" round trip.
        SecItemDelete(baseQuery as CFDictionary)

        var attributes = baseQuery
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock

        SecItemAdd(attributes as CFDictionary, nil)
    }

    static func clear() {
        SecItemDelete(baseQuery as CFDictionary)
    }
}
