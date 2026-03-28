import Foundation

/// Mixes two audio streams (screen + mic) into a single coherent PCM stream.
///
/// Both ScreenAudioCapture and MicCapture produce 16 kHz mono Int16 LE samples.
/// Writing both directly to the FIFO produces interleaved chunks from different
/// sources — Deepgram sees random context-switching noise, not speech.
///
/// AudioMixer accumulates samples from each source into separate buffers, then
/// a 20 ms timer fires and mixes them sample-by-sample (additive with clamping)
/// before writing one coherent chunk to PipeWriter.
///
/// If only one source has data for a given flush, those samples pass through
/// unmixed. If neither has data, nothing is written (no silence padding).
final class AudioMixer {
    private let pipeWriter: PipeWriter
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
    private var meterBytesOut: Int = 0
    private var meterScreenIn: Int = 0
    private var meterMicIn: Int = 0
    private var meterStart: UInt64 = 0

    init(pipeWriter: PipeWriter) {
        self.pipeWriter = pipeWriter
    }

    // MARK: - Lifecycle

    func start() {
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

    func stop() {
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

    /// Mix available samples and write to PipeWriter.
    /// Called on `queue` by the timer — never re-entrant.
    ///
    /// Outputs exactly `samplesPerFlush` (320) samples per tick, locking the
    /// output rate to 32 KB/s (1× realtime at 16 kHz mono Int16). Takes up to
    /// 320 samples from each buffer, mixes them, and writes the result.
    /// Remaining samples stay in the buffer for the next flush.
    private func _flush() {
        let screenBytes = screenBuffer.count
        let micBytes = micBuffer.count

        // Both empty — nothing to do
        guard screenBytes > 0 || micBytes > 0 else { return }

        let screenSamples = min(screenBytes / MemoryLayout<Int16>.size, samplesPerFlush)
        let micSamples = min(micBytes / MemoryLayout<Int16>.size, samplesPerFlush)

        // Fixed-size output: exactly samplesPerFlush samples per tick.
        // Zero-initialized — sources that have fewer samples contribute silence.
        var output = [Int16](repeating: 0, count: samplesPerFlush)

        // Read up to samplesPerFlush screen samples
        if screenSamples > 0 {
            screenBuffer.withUnsafeBytes { raw in
                let samples = raw.bindMemory(to: Int16.self)
                for i in 0..<screenSamples {
                    output[i] = samples[i]
                }
            }
            screenBuffer.removeFirst(screenSamples * MemoryLayout<Int16>.size)
        }

        // Add up to samplesPerFlush mic samples (additive mixing with clamping)
        if micSamples > 0 {
            micBuffer.withUnsafeBytes { raw in
                let samples = raw.bindMemory(to: Int16.self)
                for i in 0..<micSamples {
                    let mixed = Int32(output[i]) + Int32(samples[i])
                    output[i] = Int16(clamping: mixed)
                }
            }
            micBuffer.removeFirst(micSamples * MemoryLayout<Int16>.size)
        }

        let writeData = output.withUnsafeBufferPointer { Data(buffer: $0) }
        pipeWriter.write(writeData)
        meterBytesOut += writeData.count

        // Log rate every ~5 seconds
        let now = DispatchTime.now().uptimeNanoseconds
        if meterStart == 0 { meterStart = now }
        let elapsedNs = now - meterStart
        if elapsedNs >= 5_000_000_000 {
            let elapsedS = Double(elapsedNs) / 1_000_000_000
            fputs(String(format: "AudioMixer: %.1fs — screen_in=%.0f B/s  mic_in=%.0f B/s  out=%.0f B/s (%.1fx)\n",
                         elapsedS,
                         Double(meterScreenIn) / elapsedS,
                         Double(meterMicIn) / elapsedS,
                         Double(meterBytesOut) / elapsedS,
                         Double(meterBytesOut) / elapsedS / 32000.0), stderr)
            meterBytesOut = 0
            meterScreenIn = 0
            meterMicIn = 0
            meterStart = now
        }
    }
}
