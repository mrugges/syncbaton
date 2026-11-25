import Foundation
import Testing

@testable import SyncbatonCore

final class StubProtocol: URLProtocol, @unchecked Sendable {
    // Keyed by host so suites that run in parallel don't share state.
    private static let lock = NSLock()
    nonisolated(unsafe) private static var handlers: [String: @Sendable (URLRequest) -> (Int, String)] = [:]
    nonisolated(unsafe) private static var requests: [String: URLRequest] = [:]
    static func setHandler(host: String, _ h: @escaping @Sendable (URLRequest) -> (Int, String)) {
        lock.withLock { handlers[host] = h }
    }
    static func lastRequest(host: String) -> URLRequest? { lock.withLock { requests[host] } }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var req = request
        if req.httpBody == nil, let s = req.httpBodyStream {
            s.open()
            var data = Data()
            var buf = [UInt8](repeating: 0, count: 1024)
            while s.hasBytesAvailable {
                let n = s.read(&buf, maxLength: buf.count)
                if n <= 0 { break }
                data.append(buf, count: n)
            }
            req.httpBody = data
        }
        let host = req.url?.host ?? ""
        StubProtocol.lock.withLock { StubProtocol.requests[host] = req }
        let handler = StubProtocol.lock.withLock { StubProtocol.handlers[host] }!
        let (status, body) = handler(req)
        let resp = HTTPURLResponse(url: req.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: resp, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

let projectJSON = """
    {"name":"Song A.logicx","holder":"alex","since":"2026-01-02T03:04:05Z","heartbeat":"2026-01-02T03:10:00Z","stale":false,
     "last_checkin":{"user":"sam","at":"2026-01-01T20:00:00Z","note":"new bass"}}
    """

@Suite(.serialized) struct ServerClientTests {
    func client(_ status: Int, _ body: String) -> ServerClient {
        StubProtocol.setHandler(host: "syncbaton.example") { _ in (status, body) }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [StubProtocol.self]
        return ServerClient(
            baseURL: URL(string: "http://syncbaton.example:8790")!, token: "tok",
            session: URLSession(configuration: cfg))
    }

    @Test func infoDecodes() async throws {
        let c = client(
            200,
            #"{"user":"alex","device_id":"DEV","folders":[{"id":"music","label":"music"}],"stale_after_seconds":900}"#)
        let info = try await c.info()
        #expect(info.user == "alex")
        #expect(info.deviceId == "DEV")
        #expect(info.folders == [ServerFolder(id: "music")])
        #expect(info.staleAfterSeconds == 900)
        #expect(
            StubProtocol.lastRequest(host: "syncbaton.example")?.value(forHTTPHeaderField: "Authorization")
                == "Bearer tok")
    }

    @Test func projectsDecodeAndEncodePath() async throws {
        let c = client(200, #"{"projects":[\#(projectJSON)]}"#)
        let ps = try await c.projects(folder: "my music")
        #expect(ps.count == 1)
        #expect(ps[0].holder == "alex")
        #expect(ps[0].since == ISO8601DateFormatter().date(from: "2026-01-02T03:04:05Z"))
        #expect(ps[0].lastCheckin?.note == "new bass")
        #expect(
            StubProtocol.lastRequest(host: "syncbaton.example")?.url?.absoluteString.hasSuffix(
                "/folders/my%20music/projects") == true)
    }

    @Test func unsafeProjectNamesAreDropped() async throws {
        func p(_ name: String) -> String {
            #"{"name":\#(String(decoding: try! JSONEncoder().encode(name), as: UTF8.self)),"holder":null,"since":null,"heartbeat":null,"stale":false,"last_checkin":null}"#
        }
        let names = ["Good.logicx", "a/b", "..", ".", "x\u{0}y", "", "back\\slash"]
        let c = client(200, #"{"projects":[\#(names.map(p).joined(separator: ","))]}"#)
        let got = try await c.projects(folder: "music").map(\.name)
        #expect(got == ["Good.logicx", "back\\slash"])
    }

    @Test func checkinHasThirtySecondTimeout() async throws {
        let c = client(200, projectJSON)
        _ = try await c.checkin(folder: "m", project: "A", note: "")
        #expect(StubProtocol.lastRequest(host: "syncbaton.example")?.timeoutInterval == 30)
    }

    @Test func freeProjectHasNulls() async throws {
        let c = client(
            200,
            #"{"projects":[{"name":"B.logicx","holder":null,"since":null,"heartbeat":null,"stale":false,"last_checkin":null}]}"#
        )
        let p = try await c.projects(folder: "music")[0]
        #expect(p.holder == nil && p.since == nil && p.lastCheckin == nil)
    }

    @Test func checkoutSendsTakeOverAndEncodesName() async throws {
        let c = client(200, projectJSON)
        _ = try await c.checkout(folder: "music", project: "Song A.logicx", takeOver: true)
        let r = StubProtocol.lastRequest(host: "syncbaton.example")!
        #expect(r.httpMethod == "POST")
        #expect(r.url?.absoluteString.hasSuffix("/folders/music/projects/Song%20A.logicx/checkout") == true)
        #expect(String(data: r.httpBody ?? Data(), encoding: .utf8)?.contains("\"take_over\":true") == true)
    }

    @Test func checkinSendsNote() async throws {
        let c = client(200, projectJSON)
        _ = try await c.checkin(folder: "music", project: "Song A.logicx", note: "mix")
        #expect(
            String(data: StubProtocol.lastRequest(host: "syncbaton.example")?.httpBody ?? Data(), encoding: .utf8)?
                .contains("\"note\":\"mix\"") == true)
    }

    @Test func heartbeatPosts() async throws {
        let c = client(200, projectJSON)
        _ = try await c.heartbeat(folder: "music", project: "Song A.logicx")
        #expect(StubProtocol.lastRequest(host: "syncbaton.example")?.url?.path.hasSuffix("/heartbeat") == true)
    }

    @Test func heldError() async {
        let c = client(409, #"{"error":"held","holder":"alex","since":"2026-01-02T03:04:05Z"}"#)
        await #expect(
            throws: ServerError.held(holder: "alex", since: ISO8601DateFormatter().date(from: "2026-01-02T03:04:05Z"))
        ) {
            _ = try await c.checkout(folder: "music", project: "A.logicx", takeOver: false)
        }
    }

    @Test func notStaleError() async {
        let c = client(409, #"{"error":"not_stale","holder":"alex","since":"2026-01-02T03:04:05Z"}"#)
        await #expect(
            throws: ServerError.notStale(
                holder: "alex", since: ISO8601DateFormatter().date(from: "2026-01-02T03:04:05Z"))
        ) {
            _ = try await c.checkout(folder: "music", project: "A.logicx", takeOver: true)
        }
    }

    @Test func notHolderError() async {
        let c = client(409, #"{"error":"not_holder"}"#)
        await #expect(throws: ServerError.notHolder) { _ = try await c.heartbeat(folder: "m", project: "A") }
    }

    @Test func stillSyncingError() async {
        let c = client(409, #"{"error":"still_syncing","files":["A.logicx/x.wav","A.logicx/y.wav"]}"#)
        await #expect(throws: ServerError.stillSyncing(files: ["A.logicx/x.wav", "A.logicx/y.wav"])) {
            _ = try await c.checkin(folder: "m", project: "A", note: "")
        }
    }

    @Test func nullDeviceIdDecodes() async throws {
        let c = client(200, #"{"user":"alex","device_id":null,"folders":[],"stale_after_seconds":900}"#)
        #expect(try await c.info().deviceId == nil)
    }

    @Test func syncthingUnavailableError() async {
        let c = client(503, #"{"error":"syncthing_unavailable"}"#)
        await #expect(throws: ServerError.syncthingUnavailable) {
            _ = try await c.checkin(folder: "m", project: "A", note: "")
        }
    }

    @Test func unauthorizedError() async {
        let c = client(401, #"{"error":"unauthorized"}"#)
        await #expect(throws: ServerError.unauthorized) { _ = try await c.info() }
    }

    @Test func notFoundError() async {
        let c = client(404, #"{"error":"not_found"}"#)
        await #expect(throws: ServerError.notFound) { _ = try await c.projects(folder: "x") }
    }

    @Test func badRequestError() async {
        let c = client(400, #"{"error":"bad_request"}"#)
        await #expect(throws: ServerError.badRequest) { _ = try await c.info() }
    }

    @Test func unknownErrorKeepsStatus() async {
        let c = client(500, "oops")
        await #expect(throws: ServerError.http(status: 500, code: nil)) { _ = try await c.info() }
    }

    @Test func eventsDecode() async throws {
        let c = client(
            200,
            #"{"events":[{"id":43,"at":"2026-01-02T03:04:05Z","type":"take_over","folder":"music","project":"Song A.logicx","user":"alex","previous_holder":"sam"},{"id":44,"at":"2026-01-02T03:05:05Z","type":"conflict","folder":"music","project":"Song A.logicx","user":null,"path":"Song A.logicx/x.sync-conflict-1.wav"}],"last_id":44}"#
        )
        let batch = try await c.events(since: 42, wait: 30)
        #expect(batch.lastId == 44)
        #expect(batch.events[0].type == .takeOver)
        #expect(batch.events[0].previousHolder == "sam")
        #expect(batch.events[1].type == .conflict)
        #expect(batch.events[1].user == nil)
        #expect(batch.events[1].path == "Song A.logicx/x.sync-conflict-1.wav")
        let q = StubProtocol.lastRequest(host: "syncbaton.example")?.url?.query ?? ""
        #expect(q.contains("since=42") && q.contains("wait=30"))
    }

    @Test func fractionalSecondsDate() async throws {
        let c = client(
            200,
            #"{"projects":[{"name":"B","holder":"a","since":"2026-01-02T03:04:05.123456Z","heartbeat":null,"stale":false,"last_checkin":null}]}"#
        )
        #expect(try await c.projects(folder: "m")[0].since != nil)
    }
}
