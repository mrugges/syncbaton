import Foundation

public enum SyncthingConfigError: Error, Equatable, Sendable {
    case unreadable(String)
    case malformed
    case missingGUI
    case insecureRemote(String)
    case missingCertificate
}

public struct SyncthingConfig: Equatable, Sendable {
    public struct Folder: Equatable, Sendable {
        public var id: String
        public var path: String
        public var url: URL { URL(fileURLWithPath: (path as NSString).expandingTildeInPath, isDirectory: true) }
    }

    public var guiAddress: String
    public var apiKey: String
    public var tls: Bool
    public var folders: [Folder]
    /// `https-cert.pem` next to config.xml, the certificate the GUI presents when TLS is on.
    public var certificateURL: URL?

    public static var defaultPath: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/Syncthing/config.xml")
    }

    private var hostAndPort: (host: String, port: String) {
        var host = guiAddress
        var port = ""
        if let colon = guiAddress.lastIndex(of: ":") {
            host = String(guiAddress[..<colon])
            port = String(guiAddress[guiAddress.index(after: colon)...])
        }
        if ["", "0.0.0.0", "[::]", "::"].contains(host) { host = "127.0.0.1" }
        return (host, port)
    }

    public var baseURL: URL? {
        let (host, port) = hostAndPort
        return URL(string: "\(tls ? "https" : "http")://\(host)\(port.isEmpty ? "" : ":" + port)")
    }

    /// Refuses to send the API key over plain HTTP to a host other than this Mac.
    public func validateForAPIKey() throws {
        let host = hostAndPort.host
        let loopback = host == "localhost" || host == "[::1]" || host == "::1" || host.hasPrefix("127.")
        if !tls && !loopback { throw SyncthingConfigError.insecureRemote(host) }
    }

    public static func load(from url: URL = defaultPath) throws -> SyncthingConfig {
        let data: Data
        do { data = try Data(contentsOf: url) } catch {
            throw SyncthingConfigError.unreadable(error.localizedDescription)
        }
        var config = try parse(data)
        config.certificateURL = url.deletingLastPathComponent().appendingPathComponent("https-cert.pem")
        return config
    }

    public static func parse(_ data: Data) throws -> SyncthingConfig {
        let parser = XMLParser(data: data)
        let handler = Handler()
        parser.delegate = handler
        guard parser.parse(), handler.error == nil else { throw SyncthingConfigError.malformed }
        guard !handler.apiKey.isEmpty, !handler.address.isEmpty else { throw SyncthingConfigError.missingGUI }
        return SyncthingConfig(
            guiAddress: handler.address, apiKey: handler.apiKey, tls: handler.tls, folders: handler.folders,
            certificateURL: nil)
    }

    private final class Handler: NSObject, XMLParserDelegate {
        var stack: [String] = []
        var folders: [Folder] = []
        var address = ""
        var apiKey = ""
        var tls = false
        var error: (any Error)?
        private var text = ""

        func parser(
            _ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?,
            attributes: [String: String]
        ) {
            stack.append(name)
            text = ""
            switch stack.joined(separator: "/") {
            case "configuration/folder":
                folders.append(
                    Folder(id: attributes["id"] ?? "", path: attributes["path"] ?? "")
                )
            case "configuration/gui":
                tls = attributes["tls"] == "true"
            default: break
            }
        }

        func parser(_ parser: XMLParser, foundCharacters string: String) { text += string }

        func parser(
            _ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?
        ) {
            let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
            switch stack.joined(separator: "/") {
            case "configuration/gui/address": address = value
            case "configuration/gui/apikey": apiKey = value
            default: break
            }
            stack.removeLast()
            text = ""
        }

        func parser(_ parser: XMLParser, parseErrorOccurred parseError: any Error) { error = parseError }
    }
}
