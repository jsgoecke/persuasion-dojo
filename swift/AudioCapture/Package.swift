// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AudioCapture",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "AudioCapture", targets: ["AudioCaptureCLI"]),
    ],
    targets: [
        .target(
            name: "AudioCaptureCore",
            path: "Sources/AudioCaptureCore",
            linkerSettings: [
                .linkedFramework("ScreenCaptureKit"),
                .linkedFramework("CoreAudio"),
                .linkedFramework("CoreMedia"),
                .linkedFramework("CoreGraphics"),
                .linkedFramework("AVFoundation"),
            ]
        ),
        .executableTarget(
            name: "AudioCaptureCLI",
            dependencies: ["AudioCaptureCore"],
            path: "Sources/AudioCaptureCLI"
        ),
        .testTarget(
            name: "AudioCaptureTests",
            dependencies: ["AudioCaptureCore"],
            path: "Tests/AudioCaptureTests"
        ),
    ]
)
