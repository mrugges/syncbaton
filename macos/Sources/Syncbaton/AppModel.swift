import AppKit
import Foundation
import Observation
import SyncbatonCore

@MainActor
@Observable
final class AppModel {
    var serverURLString: String
    var refreshInterval: Int
    private(set) var token: String
    private(set) var info: ServerInfo?
    private(set) var projects: [String: [Project]] = [:]
    private(set) var phases: [String: SessionPhase] = [:]
    private(set) var warnings: [String: Guard.Warning] = [:]
    private(set) var connectionError: String?
    private(set) var localError: String?
    var notes: [String: String] = [:]

    @ObservationIgnored private let tokenStore = KeychainTokenStore()
    @ObservationIgnored private let notifier = Notifier()
    @ObservationIgnored private var server: ServerClient?
    @ObservationIgnored private var syncthing: LocalSyncthing?
    @ObservationIgnored private var syncthingConfig: SyncthingConfig?
    @ObservationIgnored private var sessions: [String: Session] = [:]
    @ObservationIgnored private var loops: [Task<Void, Never>] = []
    @ObservationIgnored private var started = false

    init() {
        let defaults = UserDefaults.standard
        serverURLString = defaults.string(forKey: "serverURL") ?? ""
        let interval = defaults.integer(forKey: "refreshInterval")
        refreshInterval = interval > 0 ? interval : 15
        token = tokenStore.load() ?? ""
    }

    static func key(_ folder: String, _ project: String) -> String { "\(folder)/\(project)" }

    var isConfigured: Bool { (try? ServerURLPolicy.validate(serverURLString)) != nil && !token.isEmpty }
    var me: String? { info?.user }

    // MARK: - Lifecycle

    /// Returns true when settings are missing and the Settings window should open.
    func start() -> Bool {
        guard !started else { return false }
        started = true
        notifier.requestAuthorization()
        guard isConfigured else { return true }
        connect()
        return false
    }

    /// Saves the settings. Returns a message when they can't be saved, nil on success.
    func apply(serverURL: String, token newToken: String, refreshInterval newInterval: Int) -> String? {
        let url: URL
        do { url = try ServerURLPolicy.validate(serverURL) } catch {
            return (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
        let newToken = newToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard tokenStore.save(newToken) else { return "Couldn't save the token to the Keychain." }
        serverURLString = url.absoluteString
        token = newToken
        refreshInterval = max(5, newInterval)
        UserDefaults.standard.set(serverURLString, forKey: "serverURL")
        UserDefaults.standard.set(refreshInterval, forKey: "refreshInterval")
        started = true
        if isConfigured { connect() }
        return nil
    }

    private func connect() {
        for loop in loops { loop.cancel() }
        loops = []
        info = nil
        projects = [:]
        for session in sessions.values { Task { await session.stop() } }
        sessions = [:]
        phases = [:]
        guard let url = try? ServerURLPolicy.validate(serverURLString) else { return }
        let client = ServerClient(baseURL: url, token: token)
        server = client
        loadSyncthing()
        loops.append(Task { [weak self] in await self?.refreshLoop() })
        loops.append(Task { [weak self] in await self?.watchEvents(client) })
    }

    private func loadSyncthing() {
        do {
            let config = try SyncthingConfig.load()
            syncthingConfig = config
            syncthing = try LocalSyncthing(config: config)
            localError = nil
        } catch {
            syncthing = nil
            localError = "Can't read the Syncthing config: \(error.localizedDescription)"
        }
    }

    func testConnection(serverURL: String, token: String) async -> String {
        guard let url = URL(string: serverURL.trimmingCharacters(in: .whitespacesAndNewlines)), url.host != nil else {
            return "That isn't a valid URL."
        }
        do {
            let info = try await ServerClient(
                baseURL: url, token: token.trimmingCharacters(in: .whitespacesAndNewlines)
            ).info()
            return "Connected as \(info.user)."
        } catch {
            return (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    // MARK: - Refresh

    private func refreshLoop() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(for: .seconds(refreshInterval))
        }
    }

    func refresh() async {
        guard let server else { return }
        do {
            if info == nil { info = try await server.info() }
            guard let info else { return }
            var next: [String: [Project]] = [:]
            for folder in info.folders { next[folder.id] = try await server.projects(folder: folder.id) }
            projects = next
            connectionError = nil
            await updateGuard()
        } catch is CancellationError {
        } catch {
            connectionError = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    private func updateGuard() async {
        guard let me else { return }
        var next: [String: Guard.Warning] = [:]
        for (folder, list) in projects {
            let known = list.map(\.name)
            var open = Set<String>()
            var seen = Set<String>()
            for name in known {
                let adapter = AdapterRegistry.adapter(forProject: name)
                guard adapter.supportsDetection, seen.insert(String(describing: type(of: adapter))).inserted else {
                    continue
                }
                open.formUnion(await adapter.openProjects(knownProjects: known) ?? [])
            }
            for w in Guard.warnings(openProjects: open, projects: list, me: me) {
                next[Self.key(folder, w.project)] = w
            }
        }
        for (key, w) in next where warnings[key] != w {
            notifier.post(NotificationPlanner.notice(for: w), id: "guard-\(key)")
        }
        warnings = next
    }

    private func watchEvents(_ client: ServerClient) async {
        for await event in EventWatcher(server: client).stream() {
            if let me, let notice = NotificationPlanner.notice(for: event, me: me) { notifier.post(notice) }
            await refresh()
        }
    }

    // MARK: - Actions

    private func session(_ folder: String, _ project: String) -> Session? {
        let key = Self.key(folder, project)
        if let s = sessions[key] { return s }
        guard let server, let info, let syncthing, let config = syncthingConfig else { return nil }
        guard let local = config.folders.first(where: { $0.id == folder }) else {
            localError = "Folder \(folder) isn't shared with this Mac's Syncthing."
            return nil
        }
        let s = Session(
            folder: folder, project: project, projectURL: local.url.appendingPathComponent(project),
            me: info.user, server: server, syncthing: syncthing,
            adapter: AdapterRegistry.adapter(forProject: project),
            knownProjects: { [weak self] in await self?.knownProjects(in: folder) ?? [] },
            onChange: { [weak self] phase in
                Task { @MainActor in self?.phaseChanged(key: key, project: project, phase: phase) }
            })
        sessions[key] = s
        return s
    }

    private func knownProjects(in folder: String) -> [String] { projects[folder]?.map(\.name) ?? [] }

    private func phaseChanged(key: String, project: String, phase: SessionPhase) {
        phases[key] = phase
        if phase == .closing {
            notifier.post(
                Notice(title: "\(Project.displayName(for: project)) was closed", body: "Add a note to hand it back."),
                id: "closed-\(key)")
        }
    }

    func phase(_ folder: String, _ project: String) -> SessionPhase { phases[Self.key(folder, project)] ?? .free }

    func open(folder: String, project: String, takeOver: Bool) {
        guard let s = session(folder, project) else { return }
        Task {
            if AdapterRegistry.adapter(forProject: project).needsAccessibility { promptForAccessibilityOnce() }
            await s.checkOut(takeOver: takeOver)
            await refresh()
        }
    }

    func done(folder: String, project: String) {
        guard let s = session(folder, project) else { return }
        Task { await s.done() }
    }

    func submit(folder: String, project: String) {
        guard let s = session(folder, project) else { return }
        let key = Self.key(folder, project)
        let note = notes[key] ?? ""
        Task {
            await s.submit(note: note)
            if case .free = await s.phase { notes[key] = nil }
            await refresh()
        }
    }

    func cancel(folder: String, project: String) {
        guard let s = session(folder, project) else { return }
        Task { await s.cancel() }
    }

    func dismiss(folder: String, project: String) {
        guard let s = session(folder, project) else { return }
        Task { await s.dismissFailure() }
    }

    // MARK: - Quit

    private var heldOpen: [(folder: String, project: String)] {
        phases.compactMap { key, phase in
            guard phase == .open || phase == .closing else { return nil }
            let parts = key.split(separator: "/", maxSplits: 1).map(String.init)
            return parts.count == 2 ? (parts[0], parts[1]) : nil
        }
    }

    /// Quits, after asking when a project is still checked out to this user and open or waiting for a note.
    func requestQuit() {
        let held = heldOpen
        guard !held.isEmpty else {
            NSApplication.shared.terminate(nil)
            return
        }
        let names = held.map { Project.displayName(for: $0.project) }.sorted().joined(separator: ", ")
        NSApp.activate()
        let alert = NSAlert()
        alert.messageText =
            "\(names) \(held.count == 1 ? "is" : "are") checked out to you; check \(held.count == 1 ? "it" : "them") in first?"
        alert.informativeText = "Quitting leaves the lock held until someone takes it over."
        alert.addButton(withTitle: "Check in")
        alert.addButton(withTitle: "Quit anyway")
        if alert.runModal() == .alertFirstButtonReturn {
            for h in held { done(folder: h.folder, project: h.project) }
        } else {
            NSApplication.shared.terminate(nil)
        }
    }

    // MARK: - Accessibility

    var accessibilityTrusted: Bool { LogicAdapter.isAccessibilityTrusted }

    func promptForAccessibilityOnce() {
        guard !LogicAdapter.isAccessibilityTrusted, !UserDefaults.standard.bool(forKey: "accessibilityPrompted") else {
            return
        }
        UserDefaults.standard.set(true, forKey: "accessibilityPrompted")
        NSApp.activate()
        let alert = NSAlert()
        alert.messageText = "Allow Accessibility access?"
        alert.informativeText =
            "Syncbaton reads Logic's window titles to notice when you close a project, so it can ask for your note and hand the project back. "
            + "Without it you press Done yourself. Nothing else is read."
        alert.addButton(withTitle: "Continue")
        alert.addButton(withTitle: "Not now")
        if alert.runModal() == .alertFirstButtonReturn { LogicAdapter.requestAccessibility() }
    }

    var statusSymbol: String {
        if !warnings.isEmpty { return "exclamationmark.triangle.fill" }
        if connectionError != nil { return "wifi.exclamationmark" }
        return "arrow.triangle.2.circlepath"
    }
}
