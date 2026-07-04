// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "LuluApp",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "LuluApp", targets: ["LuluApp"]),
    ],
    targets: [
        .executableTarget(
            name: "LuluApp",
            path: "Sources/LuluApp"
        ),
    ]
)

