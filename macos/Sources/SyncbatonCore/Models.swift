import Foundation

public struct LastCheckin: Codable, Equatable, Sendable {
    public var user: String
    public var at: Date
    public var note: String

    public init(user: String, at: Date, note: String) {
        self.user = user
        self.at = at
        self.note = note
    }
}

public struct Project: Codable, Equatable, Sendable, Identifiable {
    public var name: String
    public var holder: String?
    public var since: Date?
    public var heartbeat: Date?
    public var stale: Bool
    public var lastCheckin: LastCheckin?

    public var id: String { name }

    /// Name without the extension, for display ("Song A.logicx" becomes "Song A").
    public var displayName: String { Project.displayName(for: name) }

    public static func displayName(for name: String) -> String {
        (name as NSString).deletingPathExtension
    }

    public init(
        name: String, holder: String?, since: Date?, heartbeat: Date?, stale: Bool, lastCheckin: LastCheckin?
    ) {
        self.name = name
        self.holder = holder
        self.since = since
        self.heartbeat = heartbeat
        self.stale = stale
        self.lastCheckin = lastCheckin
    }
}

public struct ServerFolder: Codable, Equatable, Sendable, Identifiable {
    public var id: String

    public init(id: String) {
        self.id = id
    }
}

public struct ServerInfo: Codable, Equatable, Sendable {
    public var user: String
    public var deviceId: String?
    public var folders: [ServerFolder]
    public var staleAfterSeconds: Int

    public init(user: String, deviceId: String?, folders: [ServerFolder], staleAfterSeconds: Int) {
        self.user = user
        self.deviceId = deviceId
        self.folders = folders
        self.staleAfterSeconds = staleAfterSeconds
    }
}

public struct ServerEvent: Codable, Equatable, Sendable {
    public enum Kind: String, Codable, Sendable {
        case checkout
        case checkin
        case takeOver = "take_over"
        case conflict
        case unknown

        public init(from decoder: any Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = Kind(rawValue: raw) ?? .unknown
        }
    }

    public var id: Int
    public var at: Date
    public var type: Kind
    public var folder: String
    public var project: String
    public var user: String?
    public var note: String?
    public var previousHolder: String?
    public var path: String?

    public init(
        id: Int, at: Date, type: Kind, folder: String, project: String, user: String?, note: String?,
        previousHolder: String?, path: String?
    ) {
        self.id = id
        self.at = at
        self.type = type
        self.folder = folder
        self.project = project
        self.user = user
        self.note = note
        self.previousHolder = previousHolder
        self.path = path
    }
}

public struct EventBatch: Codable, Equatable, Sendable {
    public var events: [ServerEvent]
    public var lastId: Int

    public init(events: [ServerEvent], lastId: Int) {
        self.events = events
        self.lastId = lastId
    }
}

public struct FolderStatus: Equatable, Sendable {
    public var needBytes: Int64
    public var state: String

    public init(needBytes: Int64, state: String) {
        self.needBytes = needBytes
        self.state = state
    }
}

public struct Completion: Equatable, Sendable {
    public var completion: Double
    public var needBytes: Int64

    public init(completion: Double, needBytes: Int64) {
        self.completion = completion
        self.needBytes = needBytes
    }
}

public enum ServerError: Error, Equatable, Sendable {
    case held(holder: String, since: Date?)
    case notStale(holder: String, since: Date?)
    case notHolder
    case stillSyncing(files: [String])
    case syncthingUnavailable
    case unauthorized
    case notFound
    case badRequest
    case http(status: Int, code: String?)
    case transport(String)
    case invalidResponse
}

extension ServerError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .held(let holder, _): "Checked out by \(holder)."
        case .notStale(let holder, _): "\(holder) is still active, so it can't be taken over yet."
        case .notHolder: "You no longer hold this project."
        case .stillSyncing: "The server hasn't received all your changes yet."
        case .syncthingUnavailable: "The server can't reach its Syncthing."
        case .unauthorized: "The server rejected your token."
        case .notFound: "The server doesn't know this folder or project."
        case .badRequest: "The server rejected the request."
        case .http(let status, let code): "Server error \(status)\(code.map { " (\($0))" } ?? "")."
        case .transport(let message): "Can't reach the server: \(message)"
        case .invalidResponse: "The server sent a response the app can't read."
        }
    }
}

public func parseRFC3339(_ string: String) -> Date? {
    let plain = ISO8601DateFormatter()
    if let d = plain.date(from: string) { return d }
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return fractional.date(from: string)
}
