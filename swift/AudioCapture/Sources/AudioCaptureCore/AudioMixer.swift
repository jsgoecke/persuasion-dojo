import Foundation

/// Splits two audio streams (screen + mic) into separate pipes for independent
/// transcription — screen audio goes to Deepgram with diarization (counterparts),
/// mic audio goes to Deepgram without diarization (always the user).
///
/// Both ScreenAudioCapture and MicCapture produce 16 kHz mono Int16 LE samples.
/// AudioMixer accumulates samples from each source into separate buffers, then
/// a 10 ms timer fires and writes each source to its own PipeWriter independently.
///
/// Each pipe gets rate-controlled output at 32 KB/s (1× realtime) when its source
/// has data. If a source has no data for a given flush, nothing is written to that
/// pipe (no silence padding).
public final class AudioMixer {
    private let systemWriter: PipeWriter
    private let micWriter: PipeWriter
    private let queue = DispatchQueue(label: "audio.mixer", qos: .userInteractive)

    // Separate accumulation buffers for each source.
    // Protected by `queue` — all access is serialised.
    private var screenBuffer = Data()
    private var micBuffer = Data()

    private var timer: DispatchSourceTimer?
    private var stopped = false

    /// Flush interval in milliseconds.  10 ms = 160 samples at 16 kHz.
    private let flushIntervalMs: Int = 10

    /// Fixed number of output samples per flush.
    /// 16 kHz × 0.010 s = 160 samples = 320 bytes.
    /// This locks the output rate to exactly 32 KB/s (1× realtime).
    private let samplesPerFlush: Int = 160

    // Rate metering — log output rate every 5 seconds
    private var meterSystemOut: Int = 0
    private var meterMicOut: Int = 0
    private var meterScreenIn: Int = 0
    private var meterMicIn: Int = 0
    private var meterStart: UInt64 = 0

    public init(systemWriter: PipeWriter, micWriter: PipeWriter) {
        self.systemWriter = systemWriter
        self.micWriter = micWriter
    }

    // MARK: - Lifecycle

    public func start() {
        queue.async { [self] in
            guard !stopped else { return }
            let t = DispatchSource.makeTimerSource(queue: queue)
            t.schedule(
                deadline: .now(),
                repeating: .milliseconds(flushIntervalMs),
                leeway: .milliseconds(2)
            )
            t.setEventHandler { [weak self] in self?._flush() }
            t.resume()
            timer = t
            fputs("AudioMixer: started (flush every \(flushIntervalMs) ms)\n", stderr)
        }
    }

    public func stop() {
        queue.sync {
            stopped = true
            timer?.cancel()
            timer = nil
            screenBuffer.removeAll()
            micBuffer.removeAll()
        }
    }

    // MARK: - Input (called from capture callbacks)

    /// Add screen-captured audio samples.  Safe to call from any thread.
    func addScreenAudio(_ data: Data) {
        guard !data.isEmpty else { return }
        queue.async { [self] in
            guard !stopped else { return }
            meterScreenIn += data.count
            screenBuffer.append(data)
            _trimBuffer(&screenBuffer, label: "screen")
        }
    }

    /// Add microphone-captured audio samples.  Safe to call from any thread.
    func addMicAudio(_ data: Data) {
        guard !data.isEmpty else { return }
        queue.async { [self] in
            guard !stopped else { return }
            meterMicIn += data.count
            micBuffer.append(data)
            _trimBuffer(&micBuffer, label: "mic")
        }
    }

    // MARK: - Private

    /// Safety cap: if a buffer grows beyond 1 second of audio (32 KB at 16 kHz
    /// mono Int16), trim the oldest samples.  This prevents unbounded memory
    /// growth if PipeWriter is blocked waiting for a reader.
    private func _trimBuffer(_ buffer: inout Data, label: String) {
        let maxBytes = 16_000 * 2  // 1 second of 16 kHz mono Int16
        if buffer.count > maxBytes {
            let excess = buffer.count - maxBytes
            buffer.removeFirst(excess)
            fputs("AudioMixer: trimmed \(excess) bytes from \(label) buffer\n", stderr)
        }
    }

    /// Write each source's samples to its own pipe independently.
    /// Called on `queue` by the timer — never re-entrant.
    ///
    /// Each pipe gets up to `samplesPerFlush` (160) samples per tick, locking
    /// the output rate to 32 KB/s per pipe (1× realtime at 16 kHz mono Int16).
    /// If a source has no data, nothing is written to that pipe.
    private func _flush() {
        let screenBytes = screenBuffer.count
        let micBytes = micBuffer.count

        // Both empty — nothing to do
        guard screenBytes > 0 || micBytes > 0 else { return }

        // Write screen audio to system pipe
        if screenBytes > 0 {
            let screenSamples = min(screenBytes / MemoryLayout<Int16>.size, samplesPerFlush)
            var output = [Int16](repeating: 0, count: screenSamples)
            screenBuffer.withUnsafeBytes { raw in
                let samples = raw.bindMemory(to: Int16.self)
                for i in 0..<screenSamples {
                    output[i] = samples[i]
                }
            }
            screenBuffer.removeFirst(screenSamples * MemoryLayout<Int16>.size)
            let writeData = output.withUnsafeBufferPointer { Data(buffer: $0) }
            systemWriter.write(writeData)
            meterSystemOut += writeData.count
        }

        // Write mic audio to mic pipe
        if micBytes > 0 {
            let micSamples = min(micBytes / MemoryLayout<Int16>.size, samplesPerFlush)
            var output = [Int16](repeating: 0, count: micSamples)
            micBuffer.withUnsafeBytes { raw in
                let samples = raw.bindMemory(to: Int16.self)
                for i in 0..<micSamples {
                    output[i] = samples[i]
                }
            }
            micBuffer.removeFirst(micSamples * MemoryLayout<Int16>.size)
            let writeData = output.withUnsafeBufferPointer { Data(buffer: $0) }
            micWriter.write(writeData)
            meterMicOut += writeData.count
        }

        // Log rate every ~5 seconds
        let now = DispatchTime.now().uptimeNanoseconds
        if meterStart == 0 { meterStart = now }
        let elapsedNs = now - meterStart
        if elapsedNs >= 5_000_000_000 {
            let elapsedS = Double(elapsedNs) / 1_000_000_000
            fputs(String(format: "AudioMixer: %.1fs — screen_in=%.0f B/s  mic_in=%.0f B/s  sys_out=%.0f B/s  mic_out=%.0f B/s\n",
                         elapsedS,
                         Double(meterScreenIn) / elapsedS,
                         Double(meterMicIn) / elapsedS,
                         Double(meterSystemOut) / elapsedS,
                         Double(meterMicOut) / elapsedS), stderr)
            meterSystemOut = 0
            meterMicOut = 0
            meterScreenIn = 0
            meterMicIn = 0
            meterStart = now
        }
    }
}
