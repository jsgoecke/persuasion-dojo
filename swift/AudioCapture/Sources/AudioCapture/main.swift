import Foundation
import ScreenCaptureKit
import AVFoundation

// ── Constants ──────────────────────────────────────────────────────────────

let systemPipePath = "/tmp/persuasion_audio.pipe"
let micPipePath = "/tmp/persuasion_mic.pipe"

// ── FIFO creation ─────────────────────────────────────────────────────────

func createFIFO(at path: String) {
    Darwin.unlink(path)
    let result = Darwin.mkfifo(path, 0o600)
    if result != 0 {
        fputs("AudioCapture: mkfifo failed (errno \(errno))\n", stderr)
        exit(1)
    }
}

// ── Signal handling ────────────────────────────────────────────────────────

signal(SIGPIPE, SIG_IGN)

let systemPipeWriter = PipeWriter(path: systemPipePath)
let micPipeWriter = PipeWriter(path: micPipePath)
let mixer = AudioMixer(systemWriter: systemPipeWriter, micWriter: micPipeWriter)
let capture = ScreenAudioCapture(mixer: mixer)
let micCapture = MicCapture()

func handleShutdown() {
    Task {
        fputs("AudioCapture: shutting down…\n", stderr)
        micCapture.stop()
        await capture.stop()
        mixer.stop()
        systemPipeWriter.stop()
        micPipeWriter.stop()
        Darwin.unlink(systemPipePath)
        Darwin.unlink(micPipePath)
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

// ── Permission check & start ────────────────────────────────────────────

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

    // Create FIFOs and start PipeWriters (each waits for reader in background).
    createFIFO(at: systemPipePath)
    createFIFO(at: micPipePath)
    systemPipeWriter.start()
    micPipeWriter.start()

    // Start the audio mixer (flushes mixed PCM to PipeWriter every 20 ms).
    mixer.start()

    // Start screen audio capture (writes to mixer).
    do {
        try await capture.start()
    } catch {
        fputs("AudioCapture: failed to start capture: \(error)\n", stderr)
        Darwin.unlink(systemPipePath)
        Darwin.unlink(micPipePath)
        exit(1)
    }

    // Start microphone capture (also writes to mixer).
    do {
        try micCapture.start(mixer: mixer)
    } catch {
        fputs("MicCapture: failed to start: \(error)\n", stderr)
    }
}

// ── Run loop ──────────────────────────────────────────────────────────────

dispatchMain()
