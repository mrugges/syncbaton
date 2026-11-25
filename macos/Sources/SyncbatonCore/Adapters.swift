import AppKit
import Foundation

/// Opens a project with the default app. It can't tell when the project is closed.
public struct GenericAdapter: AppAdapter {
    public init() {}
    public var supportsDetection: Bool { false }

    public func open(projectURL: URL) async throws {
        try await openWithWorkspace(projectURL, applicationURL: nil)
    }

    public func openProjects(knownProjects: [String]) async -> Set<String>? { nil }
}

public enum AdapterError: Error, Equatable, Sendable, LocalizedError {
    case openFailed(String, String)

    public var errorDescription: String? {
        switch self {
        case .openFailed(let name, let reason): "Couldn't open \(name): \(reason)"
        }
    }
}

/// Opens `url` and waits for the system to report success or failure.
@MainActor
func openWithWorkspace(_ url: URL, applicationURL: URL?) async throws {
    let config = NSWorkspace.OpenConfiguration()
    do {
        if let applicationURL {
            _ = try await NSWorkspace.shared.open([url], withApplicationAt: applicationURL, configuration: config)
        } else {
            _ = try await NSWorkspace.shared.open(url, configuration: config)
        }
    } catch {
        throw AdapterError.openFailed(url.lastPathComponent, error.localizedDescription)
    }
}

public enum AdapterRegistry {
    public static func adapter(forProject name: String) -> any AppAdapter {
        switch (name as NSString).pathExtension.lowercased() {
        case "logicx": LogicAdapter()
        default: GenericAdapter()
        }
    }
}
