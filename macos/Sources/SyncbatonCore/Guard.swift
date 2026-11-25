import Foundation

/// Finds projects that are open on this Mac while another user holds the lock.
public enum Guard {
    public struct Warning: Equatable, Sendable, Hashable {
        public var project: String
        public var holder: String

        public init(project: String, holder: String) {
            self.project = project
            self.holder = holder
        }
    }

    public static func warnings(openProjects: Set<String>, projects: [Project], me: String) -> [Warning] {
        projects
            .compactMap { p -> Warning? in
                guard openProjects.contains(p.name), let holder = p.holder, holder != me else { return nil }
                return Warning(project: p.name, holder: holder)
            }
            .sorted { $0.project < $1.project }
    }
}
