import Foundation

public protocol ServerAPI: Sendable {
    func info() async throws -> ServerInfo
    func projects(folder: String) async throws -> [Project]
    func checkout(folder: String, project: String, takeOver: Bool) async throws -> Project
    func heartbeat(folder: String, project: String) async throws -> Project
    func checkin(folder: String, project: String, note: String) async throws -> Project
    func events(since: Int?, wait: Int) async throws -> EventBatch
}

public protocol LocalSyncthingAPI: Sendable {
    func status(folder: String) async throws -> FolderStatus
    func scan(folder: String, sub: String) async throws
    func completion(folder: String, device: String) async throws -> Completion
}

public protocol Sleeper: Sendable {
    func sleep(for duration: Duration) async throws
}

public struct SystemSleeper: Sleeper {
    public init() {}
    public func sleep(for duration: Duration) async throws { try await Task.sleep(for: duration) }
}

public protocol AppAdapter: Sendable {
    var supportsDetection: Bool { get }
    func open(projectURL: URL) async throws
    /// True when detection needs the macOS Accessibility permission.
    var needsAccessibility: Bool { get }
    /// Which of `knownProjects` (directory names such as `Song A.logicx`) are open in the app right now,
    /// or nil when this adapter can't tell.
    func openProjects(knownProjects: [String]) async -> Set<String>?
}

extension AppAdapter {
    public var needsAccessibility: Bool { false }
}
