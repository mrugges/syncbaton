// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Syncbaton",
    platforms: [.macOS(.v15)],
    products: [
        .library(name: "SyncbatonCore", targets: ["SyncbatonCore"]),
        .executable(name: "Syncbaton", targets: ["Syncbaton"]),
    ],
    targets: [
        .target(name: "SyncbatonCore"),
        .executableTarget(name: "Syncbaton", dependencies: ["SyncbatonCore"]),
        .testTarget(name: "SyncbatonCoreTests", dependencies: ["SyncbatonCore"]),
    ]
)
