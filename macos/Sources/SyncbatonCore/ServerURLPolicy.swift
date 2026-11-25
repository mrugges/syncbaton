import Foundation

/// Plain HTTP is only accepted for hosts that are not on the open internet: this Mac, Tailscale addresses
/// and `*.ts.net` names. Everything else must be https.
public enum ServerURLPolicy {
    public enum Violation: Error, Equatable, LocalizedError {
        case invalid
        case insecure

        public var errorDescription: String? {
            switch self {
            case .invalid: "That isn't a valid http or https URL."
            case .insecure:
                "Use https. Plain http is only allowed for this Mac, Tailscale addresses (100.64.0.0/10, fd7a:115c:a1e0::/48) and *.ts.net names."
            }
        }
    }

    public static func validate(_ string: String) throws -> URL {
        let trimmed = string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(), let host = url.host, !host.isEmpty
        else { throw Violation.invalid }
        switch scheme {
        case "https": return url
        case "http":
            guard isPrivateTransport(host: host) else { throw Violation.insecure }
            return url
        default: throw Violation.invalid
        }
    }

    static func isPrivateTransport(host: String) -> Bool {
        let h = host.lowercased()
        if h == "localhost" || h.hasSuffix(".ts.net") { return true }
        var v4 = in_addr()
        if inet_pton(AF_INET, h, &v4) == 1 {
            let b = withUnsafeBytes(of: v4.s_addr) { Array($0) }
            return b[0] == 127 || (b[0] == 100 && (64...127).contains(b[1]))
        }
        var v6 = in6_addr()
        if inet_pton(AF_INET6, h, &v6) == 1 {
            let b = withUnsafeBytes(of: v6) { Array($0) }
            let loopback = b.dropLast() == ArraySlice(repeating: 0, count: 15) && b[15] == 1
            return loopback || Array(b.prefix(6)) == [0xfd, 0x7a, 0x11, 0x5c, 0xa1, 0xe0]
        }
        return false
    }
}
