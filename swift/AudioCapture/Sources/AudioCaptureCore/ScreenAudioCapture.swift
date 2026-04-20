import Foundation
import ScreenCaptureKit
import CoreMedia
import CoreAudio

// MARK: - Audio output delegate

final class AudioCaptureOutput: NSObject, SCStreamOutput {
    private let mixer: AudioMixer
    private var loggedFormat = false

    /// Target output sample rate.
    private let targetSampleRate: Double = 16_000

    init(mixer: AudioMixer) {
        self.mixer = mixer
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .audio else { return }
        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        // Determine sample count and format
        let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer)
        guard let asbd = formatDescription.map({
            CMAudioFormatDescriptionGetStreamBasicDescription($0)?.pointee
        }) ?? nil else { return }

        let sourceSampleRate = asbd.mSampleRate
        let channelCount = Int(asbd.mChannelsPerFrame)
        guard channelCount > 0, sourceSampleRate > 0 else { return }

        if !loggedFormat {
            loggedFormat = true
            fputs("AudioCaptureOutput: SCK delivers \(sourceSampleRate) Hz, \(channelCount) ch, Float32\n", stderr)
        }

        // Lock block buffer data
        var dataPointer: UnsafeMutablePointer<CChar>? = nil
        var totalLength = 0
        let status = CMBlockBufferGetDataPointer(
            blockBuffer, atOffset: 0, lengthAtOffsetOut: nil,
            totalLengthOut: &totalLength, dataPointerOut: &dataPointer
        )
        guard status == kCMBlockBufferNoErr, let data = dataPointer else { return }

        // SCKit always delivers Float32 interleaved, regardless of configuration.
        // Downmix channels → mono Float32, then resample to 16 kHz, then → Int16 LE.
        let floatSamples = totalLength / MemoryLayout<Float32>.size
        let sourceFrames = floatSamples / channelCount
        guard sourceFrames > 0 else { return }

        let floatPtr = UnsafeRawPointer(data).bindMemory(to: Float32.self, capacity: floatSamples)

        // Step 1: downmix to mono Float32 at the source sample rate
        var monoFloat = [Float32](repeating: 0, count: sourceFrames)
        for frame in 0..<sourceFrames {
            var sum: Float32 = 0
            for ch in 0..<channelCount {
                sum += floatPtr[frame * channelCount + ch]
            }
            monoFloat[frame] = sum / Float32(channelCount)
        }

        // Step 2: resample from sourceSampleRate → 16 kHz if needed
        let resampledFloat: [Float32]
        if abs(sourceSampleRate - targetSampleRate) < 1.0 {
            // Already at target rate — no resampling needed
            resampledFloat = monoFloat
        } else {
            // Linear interpolation resampling
            let ratio = targetSampleRate / sourceSampleRate
            let outputFrames = Int(Double(sourceFrames) * ratio)
            guard outputFrames > 0 else { return }
            var resampled = [Float32](repeating: 0, count: outputFrames)
            for i in 0..<outputFrames {
                let srcPos = Double(i) / ratio
                let srcIdx = Int(srcPos)
                let frac = Float32(srcPos - Double(srcIdx))
                if srcIdx + 1 < sourceFrames {
                    resampled[i] = monoFloat[srcIdx] * (1.0 - frac) + monoFloat[srcIdx + 1] * frac
                } else if srcIdx < sourceFrames {
                    resampled[i] = monoFloat[srcIdx]
                }
            }
            resampledFloat = resampled
        }

        // Step 3: Float32 → Int16 LE
        var int16Samples = [Int16](repeating: 0, count: resampledFloat.count)
        for i in 0..<resampledFloat.count {
            let clamped = max(-1.0, min(1.0, resampledFloat[i]))
            int16Samples[i] = Int16(clamped * 32767.0)
        }

        let writeData = int16Samples.withUnsafeBufferPointer {
            Data(buffer: $0)
        }
        mixer.addScreenAudio(writeData)
    }
}

// MARK: - Stream controller

public final class ScreenAudioCapture {
    private var stream: SCStream?
    private var captureOutput: AudioCaptureOutput?
    private let mixer: AudioMixer

    public init(mixer: AudioMixer) {
        self.mixer = mixer
    }

    /// Check whether Screen Recording permission has been granted.
    public static func checkPermission() async throws {
        do {
            _ = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        } catch {
            // SCShareableContent throws when permission is denied.
            throw CaptureError.permissionDenied
        }
    }

    /// Start capturing system audio.
    public func start() async throws {
        fputs("ScreenAudioCapture: configuring…\n", stderr)
        // Build SCStreamConfiguration: mono audio, 16 kHz
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.sampleRate = 16_000
        config.channelCount = 1
        // Minimise video overhead — capture at 1 fps, tiny resolution
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.width = 2
        config.height = 2

        fputs("ScreenAudioCapture: fetching shareable content…\n", stderr)
        // Capture all audio (primary display as anchor; audio is system-wide)
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false
        )
        guard let display = content.displays.first else {
            throw CaptureError.noDisplay
        }
        fputs("ScreenAudioCapture: display found, creating stream…\n", stderr)
        let filter = SCContentFilter(display: display, excludingWindows: [])

        let output = AudioCaptureOutput(mixer: mixer)
        self.captureOutput = output

        let newStream = SCStream(filter: filter, configuration: config, delegate: nil)
        try newStream.addStreamOutput(output, type: .audio, sampleHandlerQueue: nil)
        fputs("ScreenAudioCapture: starting capture…\n", stderr)

        // startCapture() can hang indefinitely if Screen Recording permission
        // was silently revoked (e.g., binary signature changed on rebuild).
        // Use a timeout to detect this and report a clear error.
        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask {
                try await newStream.startCapture()
            }
            group.addTask {
                try await Task.sleep(nanoseconds: 5_000_000_000) // 5s timeout
                throw CaptureError.permissionDenied
            }
            // Whichever finishes first wins; cancel the other.
            try await group.next()
            group.cancelAll()
        }
        self.stream = newStream

        fputs("AudioCapture: streaming started (16 kHz, mono, Int16 LE)\n", stderr)
    }

    public func stop() async {
        guard let stream else { return }
        do {
            try await stream.stopCapture()
        } catch {
            fputs("AudioCapture: stop error: \(error)\n", stderr)
        }
        self.stream = nil
    }
}

// MARK: - Errors

public enum CaptureError: Error, CustomStringConvertible {
    case permissionDenied
    case noDisplay

    public var description: String {
        switch self {
        case .permissionDenied:
            return "Screen Recording permission denied. Grant it in System Settings → Privacy & Security → Screen Recording."
        case .noDisplay:
            return "No display found — cannot create SCContentFilter."
        }
    }
}
