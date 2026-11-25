import Foundation
import Testing

@testable import SyncbatonCore

@Suite(.serialized) struct LocalSyncthingTests {
    func make(_ body: String, status: Int = 200) -> LocalSyncthing {
        StubProtocol.setHandler(host: "127.0.0.1") { _ in (status, body) }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [StubProtocol.self]
        return LocalSyncthing(
            baseURL: URL(string: "http://127.0.0.1:8384")!, apiKey: "KEY", session: URLSession(configuration: cfg))
    }

    @Test func statusDecodesAndSendsKey() async throws {
        let s = try await make(#"{"globalBytes":10,"needBytes":42,"state":"syncing"}"#).status(folder: "music")
        #expect(s == FolderStatus(needBytes: 42, state: "syncing"))
        #expect(StubProtocol.lastRequest(host: "127.0.0.1")?.value(forHTTPHeaderField: "X-API-Key") == "KEY")
        #expect(StubProtocol.lastRequest(host: "127.0.0.1")?.url?.query == "folder=music")
    }

    @Test func scanPostsWithSub() async throws {
        try await make("").scan(folder: "music", sub: "Song A.logicx")
        let r = StubProtocol.lastRequest(host: "127.0.0.1")!
        #expect(r.httpMethod == "POST")
        #expect(r.url?.path == "/rest/db/scan")
        #expect(r.url?.query?.contains("sub=Song%20A.logicx") == true)
    }

    @Test func semicolonInSubIsEncoded() async throws {
        try await make("").scan(folder: "music", sub: "A;b&c=d.logicx")
        let q = StubProtocol.lastRequest(host: "127.0.0.1")?.url?.query ?? ""
        #expect(q.contains("sub=A%3Bb%26c%3Dd.logicx"))
    }

    @Test func completionDecodes() async throws {
        let c = try await make(#"{"completion":87.5,"needBytes":1234,"globalBytes":9999}"#).completion(
            folder: "music", device: "DEV")
        #expect(c == Completion(completion: 87.5, needBytes: 1234))
        #expect(StubProtocol.lastRequest(host: "127.0.0.1")?.url?.query?.contains("device=DEV") == true)
    }

    @Test func httpErrorThrows() async {
        await #expect(throws: LocalSyncthingError.self) { _ = try await make("nope", status: 403).status(folder: "m") }
    }
}
