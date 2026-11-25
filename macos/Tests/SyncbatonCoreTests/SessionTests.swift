import Foundation
import Testing

@testable import SyncbatonCore

final class PhaseLog: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [SessionPhase] = []
    func add(_ p: SessionPhase) { lock.withLock { items.append(p) } }
    var phases: [SessionPhase] { lock.withLock { items } }
}

@Suite struct SessionTests {
    struct Rig {
        let session: Session
        let server: FakeServer
        let syncthing: FakeSyncthing
        let adapter: FakeAdapter
        let sleeper: RecordingSleeper
        let log: PhaseLog
    }

    func rig(detection: Bool = true, openSets: [Set<String>?] = [], known: [String] = []) -> Rig {
        let server = FakeServer()
        let syncthing = FakeSyncthing()
        let adapter = FakeAdapter(supportsDetection: detection, openSets: openSets)
        let sleeper = RecordingSleeper()
        let log = PhaseLog()
        let session = Session(
            folder: "music", project: "Song A.logicx",
            projectURL: URL(fileURLWithPath: "/srv/music/Song A.logicx"),
            me: "sam", server: server, syncthing: syncthing, adapter: adapter, sleeper: sleeper,
            timing: Session.Timing(
                pollInterval: .seconds(5), heartbeatEveryTicks: 3, syncPoll: .seconds(2),
                retryDelays: [.seconds(2), .seconds(4), .seconds(8)], stallTimeout: .seconds(10)),
            startMonitor: false, knownProjects: { known },
            onChange: { log.add($0) })
        return Rig(session: session, server: server, syncthing: syncthing, adapter: adapter, sleeper: sleeper, log: log)
    }

    @Test func happyPath() async throws {
        let r = rig(openSets: [["Song A.logicx"], []])
        await r.syncthing.setStatuses([
            FolderStatus(needBytes: 1000, state: "syncing"), FolderStatus(needBytes: 500, state: "syncing"),
            FolderStatus(needBytes: 0, state: "idle"),
        ])
        await r.session.checkOut(takeOver: false)
        #expect(await r.session.phase == .open)
        #expect(await r.adapter.opened == [URL(fileURLWithPath: "/srv/music/Song A.logicx")])

        await r.session.tick()  // sees project open
        #expect(await r.session.phase == .open)
        await r.session.tick()  // first read without the project
        #expect(await r.session.phase == .open)
        await r.session.tick()  // second read without the project
        #expect(await r.session.phase == .closing)

        await r.syncthing.setCompletions([
            Completion(completion: 40, needBytes: 10), Completion(completion: 100, needBytes: 0),
        ])
        await r.server.setCheckin([
            .failure(.stillSyncing(files: ["x"])), .failure(.stillSyncing(files: ["x"])), .success(makeProject()),
        ])
        await r.session.submit(note: "new bass")

        #expect(await r.session.phase == .free)
        #expect(await r.syncthing.scans == ["Song A.logicx"])
        #expect(await r.syncthing.completionDevices.first == "SERVER-DEV")
        #expect(await r.server.notes == ["new bass", "new bass", "new bass"])
        let sleeps = await r.sleeper.sleeps
        #expect(sleeps.contains(.seconds(2)) && sleeps.contains(.seconds(4)))

        let phases = r.log.phases
        #expect(phases.first == .checkingOut)
        #expect(phases.contains { if case .downloading = $0 { true } else { false } })
        #expect(phases.contains { if case .uploading = $0 { true } else { false } })
        #expect(phases.contains(.checkingIn))
        #expect(phases.last == .free)
    }

    @Test func syncthingUnavailableIsRetriedLikeStillSyncing() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.server.setCheckin([
            .failure(.syncthingUnavailable), .failure(.stillSyncing(files: [])), .success(makeProject()),
        ])
        await r.session.submit(note: "x")
        #expect(await r.session.phase == .free)
        #expect(await r.server.calls.filter { $0 == "checkin" }.count == 3)
    }

    @Test func syncthingUnavailableExhaustsRetries() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.server.setCheckin(Array(repeating: .failure(.syncthingUnavailable), count: 10))
        await r.session.submit(note: "")
        #expect(await r.session.phase == .failed(.server(.syncthingUnavailable)))
        #expect(await r.server.calls.filter { $0 == "checkin" }.count == 4)
    }

    @Test func waitsForKnownDeviceBeforeUploadGate() async {
        let r = rig()
        await r.server.setDeviceIds([nil, nil, "SERVER-DEV"])
        await r.session.checkOut(takeOver: false)
        #expect(await r.server.infoCalls == 0)  // only check-in needs the device
        await r.session.done()
        await r.session.submit(note: "")
        #expect(await r.session.phase == .free)
        #expect(await r.server.infoCalls == 3)
        #expect(await r.syncthing.scans == ["Song A.logicx"])
    }

    @Test func noScanWhenDeviceStaysUnknown() async {
        let r = rig()
        await r.server.setDeviceIds([nil])
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.session.submit(note: "")
        #expect(await r.session.phase == .failed(.local("The server can't reach its Syncthing yet.")))
        #expect(await r.syncthing.scans.isEmpty)
    }

    @Test func uploadGateUsesResolvedDevice() async {
        let r = rig()
        await r.server.setDeviceIds(["DEV-X"])
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.session.submit(note: "")
        #expect(await r.syncthing.completionDevices.first == "DEV-X")
    }

    @Test func heldByOtherDoesNotOpen() async {
        let r = rig()
        await r.server.setCheckout([.failure(.held(holder: "alex", since: nil))])
        await r.session.checkOut(takeOver: false)
        #expect(await r.session.phase == .failed(.server(.held(holder: "alex", since: nil))))
        #expect(await r.adapter.opened.isEmpty)
    }

    @Test func takeOverPassesFlag() async {
        let r = rig()
        await r.session.checkOut(takeOver: true)
        #expect(await r.server.takeOverFlags == [true])
        #expect(await r.session.phase == .open)
    }

    @Test func notStaleFailure() async {
        let r = rig()
        await r.server.setCheckout([.failure(.notStale(holder: "alex", since: nil))])
        await r.session.checkOut(takeOver: true)
        #expect(await r.session.phase == .failed(.server(.notStale(holder: "alex", since: nil))))
    }

    @Test func stillSyncingExhaustsRetries() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        let always = Result<Project, ServerError>.failure(.stillSyncing(files: ["a"]))
        await r.server.setCheckin(Array(repeating: always, count: 10))
        await r.session.submit(note: "")
        #expect(await r.session.phase == .failed(.server(.stillSyncing(files: ["a"]))))
        #expect(await r.server.calls.filter { $0 == "checkin" }.count == 4)  // first try + 3 backoffs
    }

    @Test func failedCheckinCanBeRetried() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.server.setCheckin([.failure(.http(status: 500, code: nil)), .success(makeProject())])
        await r.session.submit(note: "x")
        #expect(await r.session.phase == .failed(.server(.http(status: 500, code: nil))))
        await r.session.submit(note: "x")
        #expect(await r.session.phase == .free)
    }

    @Test func adapterWithoutDetectionNeedsManualDone() async {
        let r = rig(detection: false)
        await r.session.checkOut(takeOver: false)
        for _ in 0..<5 { await r.session.tick() }
        #expect(await r.session.phase == .open)
        await r.session.done()
        #expect(await r.session.phase == .closing)
    }

    @Test func detectionUnavailableAtRuntimeKeepsOpen() async {
        let r = rig(openSets: [nil])
        await r.session.checkOut(takeOver: false)
        await r.session.tick()
        await r.session.tick()
        #expect(await r.session.phase == .open)
    }

    @Test func doesNotCloseBeforeProjectWasSeenOpen() async {
        let r = rig(openSets: [[]])
        await r.session.checkOut(takeOver: false)
        await r.session.tick()
        #expect(await r.session.phase == .open)
    }

    @Test func heartbeatRunsDuringSlowDownload() async {
        let r = rig()
        await r.syncthing.setStatuses(
            (0..<12).map { FolderStatus(needBytes: Int64(100 - $0), state: "syncing") } + [
                FolderStatus(needBytes: 0, state: "idle")
            ])
        await r.session.checkOut(takeOver: false)
        #expect(await r.session.phase == .open)
        #expect(await r.server.calls.filter { $0 == "heartbeat" }.count >= 1)
    }

    @Test func lostLockDuringDownloadNeverOpens() async {
        let r = rig()
        await r.server.setHeartbeat(.failure(.notHolder))
        await r.syncthing.setStatuses((0..<30).map { FolderStatus(needBytes: Int64(100 - $0), state: "syncing") })
        await r.session.checkOut(takeOver: false)
        #expect(await r.session.phase == .failed(.lostLock))
        #expect(await r.adapter.opened.isEmpty)
    }

    @Test func cancelDuringDownloadKeepsLockAndOffersCheckin() async {
        let r = rig()
        await r.syncthing.setStatuses((0..<30).map { FolderStatus(needBytes: Int64(100 - $0), state: "syncing") })
        let session = r.session
        await r.sleeper.setHook { n in if n == 2 { await session.cancel() } }
        await r.session.checkOut(takeOver: false)
        #expect(await r.session.phase == .closing)
        #expect(await r.adapter.opened.isEmpty)
    }

    @Test func cancelDuringUploadReturnsToClosing() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.syncthing.setCompletions((0..<30).map { Completion(completion: Double($0), needBytes: 100) })
        let session = r.session
        await r.sleeper.setHook { n in if n == 2 { await session.cancel() } }
        await r.session.submit(note: "x")
        #expect(await r.session.phase == .closing)
        #expect(await r.server.calls.filter { $0 == "checkin" }.isEmpty)
    }

    @Test func downloadStallFails() async {
        let r = rig()
        await r.syncthing.setStatuses([FolderStatus(needBytes: 100, state: "syncing")])
        await r.session.checkOut(takeOver: false)
        guard case .failed(.local(let m)) = await r.session.phase else {
            Issue.record("expected failure")
            return
        }
        #expect(m.contains("no progress"))
        #expect(await r.adapter.opened.isEmpty)
    }

    @Test func uploadStallFails() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.syncthing.setCompletions([Completion(completion: 50, needBytes: 100)])
        await r.session.submit(note: "")
        guard case .failed(.local(let m)) = await r.session.phase else {
            Issue.record("expected failure")
            return
        }
        #expect(m.contains("no progress"))
    }

    @Test func folderErrorStateFails() async {
        let r = rig()
        await r.syncthing.setStatuses([FolderStatus(needBytes: 100, state: "error")])
        await r.session.checkOut(takeOver: false)
        guard case .failed(.local(let m)) = await r.session.phase else {
            Issue.record("expected failure")
            return
        }
        #expect(m.contains("error"))
    }

    @Test func transportErrorsAreRetriedAtCheckin() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.server.setCheckin([.failure(.transport("restarting")), .success(makeProject())])
        await r.session.submit(note: "x")
        #expect(await r.session.phase == .free)
        #expect(await r.server.calls.filter { $0 == "checkin" }.count == 2)
    }

    @Test func notHolderAfterRetryIsSuccessWhenAlreadyCheckedInByMe() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.server.setCheckin([.failure(.transport("reset")), .failure(.notHolder)])
        let done = Project(
            name: "Song A.logicx", holder: nil, since: nil, heartbeat: nil, stale: false,
            lastCheckin: LastCheckin(user: "sam", at: Date(), note: "x"))
        await r.server.setProjects([done])
        await r.session.submit(note: "x")
        #expect(await r.session.phase == .free)
    }

    @Test func notHolderAfterRetryFailsWhenSomeoneElseCheckedIn() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.server.setCheckin([.failure(.transport("reset")), .failure(.notHolder)])
        let other = Project(
            name: "Song A.logicx", holder: "alex", since: nil, heartbeat: nil, stale: false, lastCheckin: nil)
        await r.server.setProjects([other])
        await r.session.submit(note: "x")
        #expect(await r.session.phase == .failed(.server(.notHolder)))
    }

    @Test func notHolderOnFirstAttemptFails() async {
        let r = rig()
        await r.session.checkOut(takeOver: false)
        await r.session.done()
        await r.server.setCheckin([.failure(.notHolder)])
        await r.session.submit(note: "x")
        #expect(await r.session.phase == .failed(.server(.notHolder)))
    }

    @Test func closeNeedsTwoConsecutiveMisses() async {
        let r = rig(openSets: [["Song A.logicx"], [], ["Song A.logicx"], [], []])
        await r.session.checkOut(takeOver: false)
        for _ in 0..<4 { await r.session.tick() }  // open, miss, open, miss
        #expect(await r.session.phase == .open)
        await r.session.tick()  // second miss in a row
        #expect(await r.session.phase == .closing)
    }

    @Test func stopCancelsMonitoring() async {
        let r = rig()
        await r.session.stop()
        #expect(await r.session.phase == .free)
    }

    @Test func heartbeatEveryNTicks() async {
        let r = rig(detection: false)
        await r.session.checkOut(takeOver: false)
        for _ in 0..<6 { await r.session.tick() }
        #expect(await r.server.calls.filter { $0 == "heartbeat" }.count == 2)
    }

    @Test func lostLockOnHeartbeatNotHolder() async {
        let r = rig(detection: false)
        await r.server.setHeartbeat(.failure(.notHolder))
        await r.session.checkOut(takeOver: false)
        for _ in 0..<3 { await r.session.tick() }
        #expect(await r.session.phase == .failed(.lostLock))
    }

    @Test func transientHeartbeatErrorIgnored() async {
        let r = rig(detection: false)
        await r.server.setHeartbeat(.failure(.transport("offline")))
        await r.session.checkOut(takeOver: false)
        for _ in 0..<3 { await r.session.tick() }
        #expect(await r.session.phase == .open)
    }

    @Test func downloadProgressIsMonotonicFractionOfInitialNeed() {
        #expect(Session.downloadProgress(initialNeed: 1000, currentNeed: 750) == 0.25)
        #expect(Session.downloadProgress(initialNeed: 0, currentNeed: 0) == 1)
        #expect(Session.downloadProgress(initialNeed: 100, currentNeed: 500) == 0)
    }
}
