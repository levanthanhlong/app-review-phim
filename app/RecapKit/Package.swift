// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "RecapKit",
    platforms: [.macOS(.v14)],
    products: [.library(name: "RecapKit", targets: ["RecapKit"])],
    targets: [
        .target(name: "RecapKit"),
        .testTarget(name: "RecapKitTests", dependencies: ["RecapKit"]),
    ]
)
