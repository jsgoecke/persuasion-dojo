import Foundation
import ScreenCaptureKit
import AVFoundation
import AudioCaptureCore

// ── Config ─────────────────────────────────────────────────────────────────

let envPort = ProcessInfo.processInfo.environment["AUDIO_BACKEND_PORT"]
let port: UInt16 = envPort.flatMap { UInt16($0) } ?? 9090
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

let sigTermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigTermSource.setEventHandler { handleShutdown() }
sigTermSource.resume()

let sigIntSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
sigIntSource.setEventHandler { handleShutdown() }
sigIntSource.resume()

signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)

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
