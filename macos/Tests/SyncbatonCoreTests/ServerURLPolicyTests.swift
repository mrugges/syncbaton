import Testing

@testable import SyncbatonCore

@Suite struct ServerURLPolicyTests {
    @Test(arguments: [
        "https://syncbaton.example", "http://127.0.0.1:8790", "http://localhost:8790", "http://[::1]:8790",
        "http://100.64.0.1:8790", "http://100.127.255.254:8790", "http://100.101.102.103",
        "http://[fd7a:115c:a1e0::1]:8790", "http://[fd7a:115c:a1e0:ab12::5]:8790",
        "http://server.tail1234.ts.net:8790",
    ])
    func allowed(_ url: String) throws {
        _ = try ServerURLPolicy.validate(url)
    }

    @Test(arguments: [
        "http://syncbaton.example:8790", "http://192.168.1.10:8790", "http://100.63.255.255", "http://100.128.0.1",
        "http://[fd7a:115c:a1e1::1]", "http://evil-ts.net", "http://10.0.0.1",
    ])
    func httpRefused(_ url: String) {
        #expect(throws: ServerURLPolicy.Violation.insecure) { try ServerURLPolicy.validate(url) }
    }

    @Test(arguments: ["", "not a url", "ftp://syncbaton.example", "http://"])
    func invalid(_ url: String) {
        #expect(throws: ServerURLPolicy.Violation.invalid) { try ServerURLPolicy.validate(url) }
    }

    @Test func trimsWhitespace() throws {
        #expect(try ServerURLPolicy.validate("  https://syncbaton.example \n").host == "syncbaton.example")
    }
}
