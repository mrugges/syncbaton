import Foundation

public struct ServerClient: ServerAPI {
    public let baseURL: URL
    public let token: String
    private let session: URLSession

    public init(baseURL: URL, token: String, session: URLSession = URLSession(configuration: .ephemeral)) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    public func info() async throws -> ServerInfo {
        try await send("GET", ["info"])
    }

    public func projects(folder: String) async throws -> [Project] {
        struct Body: Decodable { var projects: [Project] }
        let body: Body = try await send("GET", ["folders", folder, "projects"])
        return body.projects.filter { Self.isSafeProjectName($0.name) }
    }

    public func checkout(folder: String, project: String, takeOver: Bool) async throws -> Project {
        try await send("POST", ["folders", folder, "projects", project, "checkout"], body: ["take_over": takeOver])
    }

    /// A project name becomes a URL segment and a path component, so it must be a plain single name.
    static func isSafeProjectName(_ name: String) -> Bool {
        !name.isEmpty && name != "." && name != ".." && !name.contains("/") && !name.contains("\0")
    }

    public func heartbeat(folder: String, project: String) async throws -> Project {
        try await send("POST", ["folders", folder, "projects", project, "heartbeat"])
    }

    public func checkin(folder: String, project: String, note: String) async throws -> Project {
        try await send("POST", ["folders", folder, "projects", project, "checkin"], body: ["note": note], timeout: 30)
    }

    public func events(since: Int?, wait: Int) async throws -> EventBatch {
        var query = [String]()
        if let since { query.append("since=\(since)") }
        query.append("wait=\(wait)")
        return try await send("GET", ["events"], query: query.joined(separator: "&"), timeout: TimeInterval(wait + 15))
    }

    // MARK: - Transport

    private static let segmentAllowed: CharacterSet = {
        var set = CharacterSet.urlPathAllowed
        set.remove(charactersIn: "/;")
        return set
    }()

    private func url(_ segments: [String], query: String?) throws -> URL {
        var string = baseURL.absoluteString
        while string.hasSuffix("/") { string.removeLast() }
        for segment in segments {
            string += "/" + (segment.addingPercentEncoding(withAllowedCharacters: Self.segmentAllowed) ?? segment)
        }
        if let query { string += "?" + query }
        guard let url = URL(string: string) else { throw ServerError.invalidResponse }
        return url
    }

    private func send<T: Decodable>(
        _ method: String, _ segments: [String], query: String? = nil, body: [String: any Sendable]? = nil,
        timeout: TimeInterval = 20
    ) async throws -> T {
        var request = URLRequest(url: try url(segments, query: query), timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch {
            throw ServerError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else { throw ServerError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw Self.error(status: http.statusCode, data: data) }
        do {
            return try Self.decoder.decode(T.self, from: data)
        } catch {
            throw ServerError.invalidResponse
        }
    }

    private struct ErrorBody: Decodable {
        var error: String?
        var holder: String?
        var since: Date?
        var files: [String]?

        enum CodingKeys: String, CodingKey { case error, holder, since, files }

        init(from decoder: any Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            error = try? c.decodeIfPresent(String.self, forKey: .error)
            holder = try? c.decodeIfPresent(String.self, forKey: .holder)
            files = try? c.decodeIfPresent([String].self, forKey: .files)
            since = (try? c.decodeIfPresent(String.self, forKey: .since)).flatMap { parseRFC3339($0) }
        }
    }

    static func error(status: Int, data: Data) -> ServerError {
        let body = try? JSONDecoder().decode(ErrorBody.self, from: data)
        switch body?.error {
        case "held": return .held(holder: body?.holder ?? "someone", since: body?.since)
        case "not_stale": return .notStale(holder: body?.holder ?? "someone", since: body?.since)
        case "not_holder": return .notHolder
        case "still_syncing": return .stillSyncing(files: body?.files ?? [])
        case "syncthing_unavailable": return .syncthingUnavailable
        case "unauthorized": return .unauthorized
        case "not_found": return .notFound
        case "bad_request": return .badRequest
        default:
            switch status {
            case 401: return .unauthorized
            case 404: return .notFound
            default: return .http(status: status, code: body?.error)
            }
        }
    }

    static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .custom { decoder in
            let string = try decoder.singleValueContainer().decode(String.self)
            guard let date = parseRFC3339(string) else {
                throw DecodingError.dataCorrupted(
                    .init(codingPath: decoder.codingPath, debugDescription: "Bad date \(string)"))
            }
            return date
        }
        return d
    }
}
