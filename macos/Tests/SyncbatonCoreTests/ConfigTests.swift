import Foundation
import Testing

@testable import SyncbatonCore

let fixtureConfig = """
    <configuration version="37">
        <folder id="music" label="Music" path="/Users/sam/Music/Projects" type="sendreceive">
            <device id="AAAAAAA-BBBBBBB" introducedBy=""></device>
            <device id="SERVER1-DEVICE" introducedBy=""></device>
            <minDiskFree unit="%">1</minDiskFree>
        </folder>
        <folder id="docs" label="Docs" path="~/Documents/docs" type="sendreceive"></folder>
        <device id="AAAAAAA-BBBBBBB" name="sam-mac" compression="metadata"></device>
        <device id="SERVER1-DEVICE" name="server" compression="metadata"></device>
        <gui enabled="true" tls="false" debugging="false">
            <address>127.0.0.1:8384</address>
            <apikey>test-api-key</apikey>
            <theme>default</theme>
        </gui>
        <options><listenAddress>default</listenAddress></options>
    </configuration>
    """

@Suite struct ConfigTests {
    @Test func parsesGui() throws {
        let c = try SyncthingConfig.parse(Data(fixtureConfig.utf8))
        #expect(c.apiKey == "test-api-key")
        #expect(c.guiAddress == "127.0.0.1:8384")
        #expect(c.baseURL == URL(string: "http://127.0.0.1:8384"))
    }

    @Test func parsesFolders() throws {
        let c = try SyncthingConfig.parse(Data(fixtureConfig.utf8))
        #expect(c.folders.map(\.id) == ["music", "docs"])
        #expect(c.folders[0].path == "/Users/sam/Music/Projects")
    }

    @Test func expandsTildeInFolderPath() throws {
        let c = try SyncthingConfig.parse(Data(fixtureConfig.utf8))
        #expect(c.folders[1].url.path == NSHomeDirectory() + "/Documents/docs")
    }

    @Test func tlsGuiUsesHttps() throws {
        let xml = fixtureConfig.replacingOccurrences(of: "tls=\"false\"", with: "tls=\"true\"")
        #expect(try SyncthingConfig.parse(Data(xml.utf8)).baseURL == URL(string: "https://127.0.0.1:8384"))
    }

    @Test func plainHTTPOnLoopbackIsAllowed() throws {
        try SyncthingConfig.parse(Data(fixtureConfig.utf8)).validateForAPIKey()
    }

    @Test func plainHTTPOnNonLoopbackIsRefused() throws {
        let xml = fixtureConfig.replacingOccurrences(of: "127.0.0.1:8384", with: "192.168.1.5:8384")
        let c = try SyncthingConfig.parse(Data(xml.utf8))
        #expect(throws: SyncthingConfigError.insecureRemote("192.168.1.5")) { try c.validateForAPIKey() }
    }

    @Test func wildcardGuiAddressCountsAsLoopback() throws {
        let xml = fixtureConfig.replacingOccurrences(of: "127.0.0.1:8384", with: "0.0.0.0:8384")
        try SyncthingConfig.parse(Data(xml.utf8)).validateForAPIKey()
    }

    @Test func pemDecodesToDER() {
        let pem = "-----BEGIN CERTIFICATE-----\naGVs\nbG8=\n-----END CERTIFICATE-----\n"
        #expect(PinnedCertificateDelegate.der(fromPEM: pem) == Data("hello".utf8))
        #expect(PinnedCertificateDelegate.der(fromPEM: "nothing") == nil)
    }

    @Test func missingGuiThrows() {
        #expect(throws: SyncthingConfigError.self) {
            try SyncthingConfig.parse(Data("<configuration></configuration>".utf8))
        }
    }

    @Test func malformedXMLThrows() {
        #expect(throws: SyncthingConfigError.self) {
            try SyncthingConfig.parse(Data("<configuration><gui>".utf8))
        }
    }
}
