import Foundation

public struct Notice: Equatable, Sendable {
    public var title: String
    public var body: String

    public init(title: String, body: String) {
        self.title = title
        self.body = body
    }
}

public enum NotificationPlanner {
    public static func notice(for event: ServerEvent, me: String) -> Notice? {
        let name = Project.displayName(for: event.project)
        let who = event.user ?? "someone"
        if event.type != .conflict && event.type != .unknown && event.user == me { return nil }
        switch event.type {
        case .checkout:
            return Notice(title: "\(who) checked out \(name)", body: "It is locked until they hand it back.")
        case .checkin:
            let note = event.note ?? ""
            return Notice(title: "\(who) handed back \(name)", body: note.isEmpty ? "No note" : note)
        case .takeOver:
            if event.previousHolder == me {
                return Notice(
                    title: "\(who) took over \(name) from you",
                    body: "Close it without saving, or your changes will conflict with theirs.")
            }
            return Notice(title: "\(who) took over \(name) from \(event.previousHolder ?? "someone")", body: "")
        case .conflict:
            return Notice(title: "Sync conflict in \(name)", body: event.path ?? event.project)
        case .unknown:
            return nil
        }
    }

    public static func notice(for warning: Guard.Warning) -> Notice {
        Notice(
            title: "\(Project.displayName(for: warning.project)) is checked out by \(warning.holder)",
            body: "Close it without saving.")
    }
}
