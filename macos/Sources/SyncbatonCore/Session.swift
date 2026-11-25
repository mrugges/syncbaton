import Foundation

struct DeviceUnknown: LocalizedError {
    var errorDescription: String? { "The server can't reach its Syncthing yet." }
}

struct SyncProblem: LocalizedError {
    var errorDescription: String?
}

struct LockLost: Error {}
struct CancelledByUser: Error {}

public enum SessionFailure: Equatable, Sendable {
    case server(ServerError)
    case local(String)
    case lostLock

    public var message: String {
        switch self {
        case .server(let e): e.errorDescription ?? "Server error."
        case .local(let m): m
        case .lostLock: "Someone else took this project over. Close it without saving."
        }
    }

    init(_ error: any Error) {
        if let e = error as? ServerError {
            self = .server(e)
        } else if error is LockLost {
            self = .lostLock
        } else {
            self = .local((error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
        }
    }
}

extension ServerError {
    fileprivate var isRetryableAtCheckin: Bool {
        switch self {
        case .stillSyncing, .syncthingUnavailable, .transport: true
        default: false
        }
    }
}

public enum SessionPhase: Equatable, Sendable {
    case free
    case checkingOut
    case downloading(Double)
    case open
    /// The project was closed (or the user pressed Done) and the app is waiting for a note.
    case closing
    case uploading(Double)
    case checkingIn
    case failed(SessionFailure)
}

/// Check-out / open / check-in flow for one project.
///
/// Sync progress uses the whole folder's `needBytes` from the local Syncthing, not only the files under
/// the project. A busy folder therefore delays the open or the check-in a little longer than needed;
/// it never lets either happen early.
public actor Session {
    public struct Timing: Sendable {
        public var pollInterval: Duration
        public var heartbeatEveryTicks: Int
        public var syncPoll: Duration
        public var retryDelays: [Duration]
        /// Waiting for Syncthing fails when its numbers haven't changed for this long.
        public var stallTimeout: Duration

        public init(
            pollInterval: Duration = .seconds(5), heartbeatEveryTicks: Int = 12, syncPoll: Duration = .seconds(2),
            retryDelays: [Duration] = [.seconds(2), .seconds(4), .seconds(8), .seconds(16)]
                + Array(repeating: .seconds(30), count: 20),
            stallTimeout: Duration = .seconds(600)
        ) {
            self.pollInterval = pollInterval
            self.heartbeatEveryTicks = heartbeatEveryTicks
            self.syncPoll = syncPoll
            self.retryDelays = retryDelays
            self.stallTimeout = stallTimeout
        }
    }

    public let folder: String
    public let project: String
    public private(set) var phase: SessionPhase = .free {
        didSet { if phase != oldValue { onChange(phase) } }
    }

    private let projectURL: URL
    private let me: String
    private let server: any ServerAPI
    private let syncthing: any LocalSyncthingAPI
    private let adapter: any AppAdapter
    private let sleeper: any Sleeper
    private let timing: Timing
    private let startMonitor: Bool
    private let knownProjects: @Sendable () async -> [String]
    private let onChange: @Sendable (SessionPhase) -> Void
    private var serverDeviceId: String?
    private var ticks = 0
    private var misses = 0
    private var seenOpen = false
    private var cancelRequested = false
    private var sinceHeartbeat = Duration.zero
    private var monitor: Task<Void, Never>?

    public init(
        folder: String, project: String, projectURL: URL, me: String, server: any ServerAPI,
        syncthing: any LocalSyncthingAPI, adapter: any AppAdapter, sleeper: any Sleeper = SystemSleeper(),
        timing: Timing = Timing(), startMonitor: Bool = true,
        knownProjects: @escaping @Sendable () async -> [String] = { [] },
        onChange: @escaping @Sendable (SessionPhase) -> Void = { _ in }
    ) {
        self.folder = folder
        self.project = project
        self.projectURL = projectURL
        self.me = me
        self.server = server
        self.syncthing = syncthing
        self.adapter = adapter
        self.sleeper = sleeper
        self.timing = timing
        self.startMonitor = startMonitor
        self.knownProjects = knownProjects
        self.onChange = onChange
    }

    public static func downloadProgress(initialNeed: Int64, currentNeed: Int64) -> Double {
        guard initialNeed > 0 else { return 1 }
        return min(1, max(0, 1 - Double(currentNeed) / Double(initialNeed)))
    }

    // MARK: - Check out

    /// Takes the lock, waits until the local Syncthing has everything, then opens the project.
    public func checkOut(takeOver: Bool) async {
        switch phase {
        case .free, .failed: break
        default: return
        }
        cancelRequested = false
        sinceHeartbeat = .zero
        phase = .checkingOut
        do {
            _ = try await server.checkout(folder: folder, project: project, takeOver: takeOver)
            try await waitForDownload()
            try await adapter.open(projectURL: projectURL)
        } catch is CancelledByUser {
            phase = .closing
            return
        } catch {
            phase = .failed(SessionFailure(error))
            return
        }
        ticks = 0
        misses = 0
        seenOpen = false
        phase = .open
        if startMonitor { startMonitoring() }
    }

    private func waitForDownload() async throws {
        phase = .downloading(0)
        var initial: Int64?
        var stall = StallCounter(timing)
        while true {
            try checkCancelled()
            let status = try await syncthing.status(folder: folder)
            try checkFolderState(status)
            if status.needBytes == 0 { return }
            let first = initial ?? status.needBytes
            initial = first
            try stall.record(Double(status.needBytes))
            phase = .downloading(Session.downloadProgress(initialNeed: first, currentNeed: status.needBytes))
            try await waitAndBeat()
        }
    }

    private func checkFolderState(_ status: FolderStatus) throws {
        if status.state == "error" { throw SyncProblem(errorDescription: "Syncthing reports an error on this folder.") }
    }

    private func checkCancelled() throws {
        if cancelRequested { throw CancelledByUser() }
    }

    /// Sleeps one sync poll and sends a heartbeat when one is due, so the lock doesn't go stale while
    /// a long transfer runs.
    private func waitAndBeat() async throws {
        try await sleeper.sleep(for: timing.syncPoll)
        sinceHeartbeat += timing.syncPoll
        guard sinceHeartbeat >= timing.pollInterval * timing.heartbeatEveryTicks else { return }
        sinceHeartbeat = .zero
        do {
            _ = try await server.heartbeat(folder: folder, project: project)
        } catch ServerError.notHolder {
            throw LockLost()
        } catch {
            // Transient failures are retried on the next heartbeat.
        }
    }

    /// Stops waiting for Syncthing. The lock stays with the user and the project moves to the note step.
    public func cancel() { cancelRequested = true }

    // MARK: - While open

    private func startMonitoring() {
        monitor?.cancel()
        let interval = timing.pollInterval
        let sleeper = sleeper
        monitor = Task { [weak self] in
            while !Task.isCancelled {
                do { try await sleeper.sleep(for: interval) } catch { return }
                guard let self else { return }
                await self.tick()
            }
        }
    }

    private func stopMonitoring() {
        monitor?.cancel()
        monitor = nil
    }

    /// Ends the background monitor. Used when the app reconfigures or quits.
    public func stop() { stopMonitoring() }

    /// One monitor step: heartbeat every `heartbeatEveryTicks` ticks, then close detection.
    public func tick() async {
        guard phase == .open else { return }
        ticks += 1
        if ticks % timing.heartbeatEveryTicks == 0 {
            do {
                _ = try await server.heartbeat(folder: folder, project: project)
            } catch ServerError.notHolder {
                stopMonitoring()
                phase = .failed(.lostLock)
                return
            } catch {
                // Transient failures are retried on the next heartbeat; the server allows a long grace period.
            }
        }
        guard phase == .open, adapter.supportsDetection else { return }
        let known = Array(Set(await knownProjects() + [project]))
        guard let open = await adapter.openProjects(knownProjects: known) else { return }
        if open.contains(project) {
            seenOpen = true
            misses = 0
        } else if seenOpen {
            misses += 1
            if misses >= 2 {
                stopMonitoring()
                phase = .closing
            }
        }
    }

    /// Manual "Done". Also used to resume a check-in for a project the server says this user holds
    /// but this app session didn't open.
    public func done() {
        switch phase {
        case .open, .free, .failed:
            stopMonitoring()
            phase = .closing
        default: break
        }
    }

    public func dismissFailure() {
        if case .failed = phase { phase = .free }
    }

    // MARK: - Check in

    public func submit(note: String) async {
        switch phase {
        case .closing, .failed: break
        default: return
        }
        cancelRequested = false
        sinceHeartbeat = .zero
        phase = .uploading(0)
        do {
            let device = try await resolveDeviceID()
            // POST /rest/db/scan returns after the scan has finished, so no scan-state polling is needed.
            try await syncthing.scan(folder: folder, sub: project)
            try await waitForUpload(device: device)
            phase = .checkingIn
            try await checkinWithRetry(note: note)
            phase = .free
        } catch is CancelledByUser {
            phase = .closing
        } catch {
            phase = .failed(SessionFailure(error))
        }
    }

    /// The server's Syncthing device ID, which `/info` reports as null while the server can't reach its
    /// Syncthing. Asks again with the retry delays; fails when it stays unknown.
    private func resolveDeviceID() async throws -> String {
        if let serverDeviceId { return serverDeviceId }
        var delays = timing.retryDelays[...]
        while true {
            try checkCancelled()
            if let id = try await server.info().deviceId {
                serverDeviceId = id
                return id
            }
            guard let delay = delays.popFirst() else { throw DeviceUnknown() }
            try await sleeper.sleep(for: delay)
        }
    }

    private func waitForUpload(device: String) async throws {
        var stall = StallCounter(timing)
        while true {
            try checkCancelled()
            try checkFolderState(try await syncthing.status(folder: folder))
            let c = try await syncthing.completion(folder: folder, device: device)
            if c.completion >= 100 && c.needBytes == 0 { return }
            try stall.record(c.completion + Double(c.needBytes) * 1e-3)
            phase = .uploading(min(1, max(0, c.completion / 100)))
            try await waitAndBeat()
        }
    }

    private func checkinWithRetry(note: String) async throws {
        var delays = timing.retryDelays[...]
        var retried = false
        while true {
            do {
                _ = try await server.checkin(folder: folder, project: project, note: note)
                return
            } catch ServerError.notHolder where retried {
                // An earlier attempt may have succeeded and only its answer was lost.
                if await alreadyCheckedIn() { return }
                throw ServerError.notHolder
            } catch let error as ServerError where error.isRetryableAtCheckin {
                guard let delay = delays.popFirst() else { throw error }
                retried = true
                try await sleeper.sleep(for: delay)
            }
        }
    }

    private func alreadyCheckedIn() async -> Bool {
        guard let list = try? await server.projects(folder: folder), let p = list.first(where: { $0.name == project })
        else { return false }
        return p.holder == nil && p.lastCheckin?.user == me
    }
}

/// Counts consecutive polls where a progress value didn't change.
private struct StallCounter {
    private let limit: Int
    private let timeout: Duration
    private var last: Double?
    private var idle = 0

    init(_ timing: Session.Timing) {
        limit = max(1, Int(timing.stallTimeout / timing.syncPoll))
        timeout = timing.stallTimeout
    }

    mutating func record(_ value: Double) throws {
        idle = value == last ? idle + 1 : 0
        last = value
        if idle >= limit {
            let seconds = Int(timeout.components.seconds)
            let span = seconds >= 60 ? "\(seconds / 60) min" : "\(seconds) s"
            throw SyncProblem(errorDescription: "Syncing made no progress for \(span).")
        }
    }
}
