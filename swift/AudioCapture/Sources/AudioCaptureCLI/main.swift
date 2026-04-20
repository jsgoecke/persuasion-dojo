import Foundation
import ScreenCaptureKit
import AVFoundation
import AudioCaptureCore

// ── Config ─────────────────────────────────────────────────────────────────

let envPort = ProcessInfo.processInfo.environment["AUDIO_BACKEND_PORT"]
let port: UInt16
if let raw = envPort, !raw.isEmpty {
    // Env var is present — parse strictly. A typo silently defaulting to 9090
    // causes Python and Swift to diverge on which port they're using, which is
    // very hard to debug.
    guard let parsed = UInt16(raw), parsed > 0 else {
        fputs("AudioCapture: AUDIO_BACKEND_PORT must be a non-zero UInt16, got \(raw)\n",
              stderr)
        exit(2)
    }
    port = parsed
} else {
    port = 9090
}
let host = "127.0.0.1"

fputs("AudioCapture: target \(host):\(port)\n", stderr)

// ── Signal handling ────────────────────────────────────────────────────────

signal(SIGPIPE, SIG_IGN)

let systemWriter = TcpStreamWriter(host: host, port: port, streamTag: 0x01)
let micWriter    = TcpStreamWriter(host: host, port: port, streamTag: 0x02)
let mixer        = AudioMixer(systemWriter: systemWriter, micWriter: micWriter)
let capture      = ScreenAudioCapture(mixer: mixer)
let micCapture   = MicCapture()

func handleShutdown() {
    Task {
        fputs("AudioCapture: shutting down…\n", stderr)
        micCapture.stop()
        await capture.stop()
        mixer.stop()
        systemWriter.stop()
        micWriter.stop()
        exit(0)
    }
}

// Install SIG_IGN dispositions before creating the DispatchSources, so a signal
// delivered during setup can't run the default terminate handler in the gap
// between process start and source.resume().
signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)

let sigTermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigTermSource.setEventHandler { handleShutdown() }
sigTermSource.resume()

let sigIntSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
sigIntSource.setEventHandler { handleShutdown() }
sigIntSource.resume()

// ── Permission check & start ───────────────────────────────────────────────

Task {
    fputs("AudioCapture: checking Screen Recording permission…\n", stderr)
    do {
        try await ScreenAudioCapture.checkPermission()
    } catch CaptureError.permissionDenied {
        fputs("AudioCapture: \(CaptureError.permissionDenied)\n", stderr)
        exit(2)
    } catch {
        fputs("AudioCapture: unexpected error checking permission: \(error)\n", stderr)
        exit(1)
    }
    fputs("AudioCapture: permission OK\n", stderr)

    systemWriter.start()
    micWriter.start()

    mixer.start()

    do {
        try await capture.start()
    } catch {
        fputs("AudioCapture: failed to start capture: \(error)\n", stderr)
        exit(1)
    }

    do {
        try micCapture.start(mixer: mixer)
    } catch {
        fputs("MicCapture: failed to start: \(error)\n", stderr)
    }
}

dispatchMain()
