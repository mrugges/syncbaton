import Foundation

public enum LocalSyncthingError: Error, Equatable, Sendable {
    case http(Int)
    case transport(String)
    case invalidResponse
}

extension LocalSyncthingError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .http(let status): "Local Syncthing answered \(status)."
        case .transport(let m): "Can't reach local Syncthing: \(m)"
        case .invalidResponse: "Local Syncthing sent an unreadable response."
        }
    }
}

/// Accepts only the certificate Syncthing wrote to its config directory (`https-cert.pem`).
final class PinnedCertificateDelegate: NSObject, URLSessionDelegate, Sendable {
    private let pinned: Data

    init(pinnedDER: Data) { pinned = pinnedDER }

    /// DER bytes of the first certificate in a PEM string.
    static func der(fromPEM pem: String) -> Data? {
        guard let begin = pem.range(of: "-----BEGIN CERTIFICATE-----"),
            let end = pem.range(of: "-----END CERTIFICATE-----", range: begin.upperBound..<pem.endIndex)
        else { return nil }
        let body = pem[begin.upperBound..<end.lowerBound].filter { !$0.isWhitespace }
        return Data(base64Encoded: String(body))
    }

    func urlSession(
        _ session: URLSession, didReceive challenge: URLAuthenticationChallenge
    ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust, let trust = space.serverTrust else {
            return (.performDefaultHandling, nil)
        }
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate], let leaf = chain.first,
            (SecCertificateCopyData(leaf) as Data) == pinned
        else { return (.cancelAuthenticationChallenge, nil) }
        return (.useCredential, URLCredential(trust: trust))
    }
}

public struct LocalSyncthing: LocalSyncthingAPI {
    public let baseURL: URL
    public let apiKey: String
    private let session: URLSession

    public init(baseURL: URL, apiKey: String, session: URLSession = URLSession(configuration: .ephemeral)) {
        self.baseURL = baseURL
        self.apiKey = apiKey
        self.session = session
    }

    /// Refuses to build a client that would send the API key over plain HTTP to another host, or to a
    /// TLS endpoint whose certificate can't be pinned.
    public init(config: SyncthingConfig) throws {
        guard let url = config.baseURL else { throw SyncthingConfigError.missingGUI }
        try config.validateForAPIKey()
        var session = URLSession(configuration: .ephemeral)
        if config.tls {
            guard let certURL = config.certificateURL, let pem = try? String(contentsOf: certURL, encoding: .utf8),
                let der = PinnedCertificateDelegate.der(fromPEM: pem)
            else { throw SyncthingConfigError.missingCertificate }
            session = URLSession(
                configuration: .ephemeral, delegate: PinnedCertificateDelegate(pinnedDER: der), delegateQueue: nil)
        }
        self.init(baseURL: url, apiKey: config.apiKey, session: session)
    }

    public func status(folder: String) async throws -> FolderStatus {
        struct Body: Decodable {
            var needBytes: Int64
            var state: String
        }
        let b: Body = try await request("GET", "db/status", ["folder": folder])
        return FolderStatus(needBytes: b.needBytes, state: b.state)
    }

    public func scan(folder: String, sub: String) async throws {
        // Syncthing's POST /rest/db/scan returns only after the scan has finished (the handler waits on the
        // folder's scan request), so a long timeout replaces any polling of the folder state.
        _ = try await raw("POST", "db/scan", ["folder": folder, "sub": sub], timeout: 900)
    }

    public func completion(folder: String, device: String) async throws -> Completion {
        struct Body: Decodable {
            var completion: Double
            var needBytes: Int64
        }
        let b: Body = try await request("GET", "db/completion", ["folder": folder, "device": device])
        return Completion(completion: b.completion, needBytes: b.needBytes)
    }

    private static let valueAllowed: CharacterSet = {
        var set = CharacterSet.urlQueryAllowed
        set.remove(charactersIn: "&=+#;")
        return set
    }()

    private func raw(
        _ method: String, _ path: String, _ query: KeyValuePairs<String, String>, timeout: TimeInterval = 20
    )
        async throws -> Data
    {
        let q = query.map {
            "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: Self.valueAllowed) ?? $0.value)"
        }
        .joined(separator: "&")
        guard let url = URL(string: baseURL.absoluteString + "/rest/" + path + "?" + q) else {
            throw LocalSyncthingError.invalidResponse
        }
        var req = URLRequest(url: url, timeoutInterval: timeout)
        req.httpMethod = method
        req.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        do {
            let (data, response) = try await session.data(for: req)
            guard let http = response as? HTTPURLResponse else { throw LocalSyncthingError.invalidResponse }
            guard (200..<300).contains(http.statusCode) else { throw LocalSyncthingError.http(http.statusCode) }
            return data
        } catch let e as LocalSyncthingError {
            throw e
        } catch let e as URLError where e.code == .cancelled {
            throw CancellationError()
        } catch {
            throw LocalSyncthingError.transport(error.localizedDescription)
        }
    }

    private func request<T: Decodable>(_ method: String, _ path: String, _ query: KeyValuePairs<String, String>)
        async throws -> T
    {
        let data = try await raw(method, path, query)
        do { return try JSONDecoder().decode(T.self, from: data) } catch { throw LocalSyncthingError.invalidResponse }
    }
}
