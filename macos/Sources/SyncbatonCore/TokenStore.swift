import Foundation
import Security

/// Stores the user's server token in the login keychain.
public struct KeychainTokenStore: Sendable {
    private let service: String
    private let account: String

    public init(service: String = "io.github.syncbaton.app", account: String = "server-token") {
        self.service = service
        self.account = account
    }

    private var query: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    public func load() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    public func save(_ token: String) -> Bool {
        SecItemDelete(query as CFDictionary)
        guard !token.isEmpty else { return true }
        var q = query
        q[kSecValueData as String] = Data(token.utf8)
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }
}
