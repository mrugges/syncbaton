import AppKit
import ApplicationServices
import Foundation

/// Matches Logic window titles to project directory names.
///
/// Logic's window title is the project name followed by a view name ("Song A - Tracks", "Song A - Mixer"),
/// sometimes with an " - Edited" marker. Project names may contain " - " themselves, so a title is matched
/// against the real project names and the longest match wins ("Mix - Remix - Tracks" is
/// `Mix - Remix.logicx`, not `Mix.logicx`).
public enum LogicTitleParser {
    static let separators = [" - ", " \u{2013} ", " \u{2014} "]
    static let ext = ".logicx"

    public static func match(title: String, known: [String]) -> String? {
        let t = title.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !t.isEmpty else { return nil }
        var best: (name: String, length: Int)?
        for name in known {
            let base = name.lowercased().hasSuffix(ext) ? String(name.dropLast(ext.count)) : name
            guard !base.isEmpty else { continue }
            let lower = base.lowercased()
            for candidate in [lower, lower + ext]
            where t == candidate || separators.contains(where: { t.hasPrefix(candidate + $0) }) {
                if candidate.count > (best?.length ?? -1) { best = (name, candidate.count) }
            }
        }
        return best?.name
    }

    public static func projectNames(fromTitles titles: [String], known: [String]) -> Set<String> {
        Set(titles.compactMap { match(title: $0, known: known) })
    }
}

public struct LogicAdapter: AppAdapter {
    public static let bundleID = "com.apple.logic10"

    public init() {}
    public var supportsDetection: Bool { true }

    public static var isAccessibilityTrusted: Bool { AXIsProcessTrusted() }

    /// Shows the system Accessibility prompt.
    public static func requestAccessibility() {
        _ = AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary)
    }

    public var needsAccessibility: Bool { true }

    public func open(projectURL: URL) async throws {
        let app = await MainActor.run { NSWorkspace.shared.urlForApplication(withBundleIdentifier: Self.bundleID) }
        try await openWithWorkspace(projectURL, applicationURL: app)
    }

    public func openProjects(knownProjects: [String]) async -> Set<String>? {
        guard Self.isAccessibilityTrusted else { return nil }
        let apps = NSRunningApplication.runningApplications(withBundleIdentifier: Self.bundleID)
        var titles: [String] = []
        for app in apps {
            // A failed read is "unknown", never "closed".
            guard let t = Self.windowTitles(pid: app.processIdentifier) else { return nil }
            titles += t
        }
        return LogicTitleParser.projectNames(fromTitles: titles, known: knownProjects)
    }

    static func windowTitles(pid: pid_t) -> [String]? {
        let element = AXUIElementCreateApplication(pid)
        var windows: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXWindowsAttribute as CFString, &windows) == .success,
            let list = windows as? [AXUIElement]
        else { return nil }
        return list.compactMap { window in
            var title: CFTypeRef?
            guard AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &title) == .success else {
                return nil
            }
            return title as? String
        }
    }
}
