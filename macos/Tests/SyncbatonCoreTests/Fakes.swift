import Foundation

@testable import SyncbatonCore

func makeProject(_ name: String = "Song A.logicx", holder: String? = nil) -> Project {
    Project(name: name, holder: holder, since: nil, heartbeat: nil, stale: false, lastCheckin: nil)
}

actor FakeServer: ServerAPI {
    var checkoutResults: [Result<Project, ServerError>] = []
    var checkinResults: [Result<Project, ServerError>] = []
    var heartbeatResult: Result<Project, ServerError> = .success(makeProject(holder: "sam"))
    var eventBatches: [Result<EventBatch, ServerError>] = []
    private(set) var calls: [String] = []
    private(set) var takeOverFlags: [Bool] = []
    private(set) var notes: [String] = []
    private(set) var eventSinces: [Int?] = []

    func setCheckout(_ r: [Result<Project, ServerError>]) { checkoutResults = r }
    func setCheckin(_ r: [Result<Project, ServerError>]) { checkinResults = r }
    func setHeartbeat(_ r: Result<Project, ServerError>) { heartbeatResult = r }
    func setEvents(_ r: [Result<EventBatch, ServerError>]) { eventBatches = r }

    var deviceIds: [String?] = ["SERVER-DEV"]
    private(set) var infoCalls = 0
    func setDeviceIds(_ ids: [String?]) { deviceIds = ids }

    func info() async throws -> ServerInfo {
        infoCalls += 1
        let id = deviceIds.count > 1 ? deviceIds.removeFirst() : deviceIds[0]
        return ServerInfo(user: "sam", deviceId: id, folders: [], staleAfterSeconds: 900)
    }
    var projectsResult: [Project] = []
    func setProjects(_ p: [Project]) { projectsResult = p }
    func projects(folder: String) async throws -> [Project] { projectsResult }
    func checkout(folder: String, project: String, takeOver: Bool) async throws -> Project {
        calls.append("checkout")
        takeOverFlags.append(takeOver)
        return try (checkoutResults.isEmpty ? .success(makeProject(holder: "sam")) : checkoutResults.removeFirst())
            .get()
    }
    func heartbeat(folder: String, project: String) async throws -> Project {
        calls.append("heartbeat")
        return try heartbeatResult.get()
    }
    func checkin(folder: String, project: String, note: String) async throws -> Project {
        calls.append("checkin")
        notes.append(note)
        return try (checkinResults.isEmpty ? .success(makeProject()) : checkinResults.removeFirst()).get()
    }
    func events(since: Int?, wait: Int) async throws -> EventBatch {
        eventSinces.append(since)
        if eventBatches.isEmpty {
            try await Task.sleep(for: .seconds(60))
            return EventBatch(events: [], lastId: since ?? 0)
        }
        return try eventBatches.removeFirst().get()
    }
}

actor FakeSyncthing: LocalSyncthingAPI {
    var statuses: [FolderStatus] = [FolderStatus(needBytes: 0, state: "idle")]
    var completions: [Completion] = [Completion(completion: 100, needBytes: 0)]
    private(set) var scans: [String] = []
    private(set) var completionDevices: [String] = []

    func setStatuses(_ s: [FolderStatus]) { statuses = s }
    func setCompletions(_ c: [Completion]) { completions = c }

    func status(folder: String) async throws -> FolderStatus {
        statuses.count > 1 ? statuses.removeFirst() : statuses[0]
    }
    func scan(folder: String, sub: String) async throws { scans.append(sub) }
    func completion(folder: String, device: String) async throws -> Completion {
        completionDevices.append(device)
        return completions.count > 1 ? completions.removeFirst() : completions[0]
    }
}

actor FakeAdapter: AppAdapter {
    nonisolated let supportsDetection: Bool
    var openSets: [Set<String>?] = []
    private(set) var opened: [URL] = []

    init(supportsDetection: Bool, openSets: [Set<String>?] = []) {
        self.supportsDetection = supportsDetection
        self.openSets = openSets
    }
    func open(projectURL: URL) async throws { opened.append(projectURL) }
    func openProjects(knownProjects: [String]) async -> Set<String>? {
        if !supportsDetection { return nil }
        return openSets.count > 1 ? openSets.removeFirst() : (openSets.first ?? [])
    }
}

actor RecordingSleeper: Sleeper {
    private(set) var sleeps: [Duration] = []
    private var hook: (@Sendable (Int) async -> Void)?
    func setHook(_ h: @escaping @Sendable (Int) async -> Void) { hook = h }
    func sleep(for d: Duration) async throws {
        sleeps.append(d)
        await hook?(sleeps.count)
    }
}
