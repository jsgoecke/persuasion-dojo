import AVFoundation
import Foundation

/// Captures microphone input and forwards Int16 LE mono 16 kHz PCM to AudioMixer.
///
/// Uses AVAudioEngine to tap the default input device.  The audio is converted
/// from whatever the hardware format is to mono 16 kHz Int16 LE to match the
/// ScreenCaptureKit output so both streams share the same TCP transport.
public final class MicCapture {
    private let engine = AVAudioEngine()
    private var mixer: AudioMixer?

    /// Target format: mono 16 kHz Int16
    private let targetFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 16_000,
        channels: 1,
        interleaved: true
    )!

    public init() {}

    /// Start capturing the default microphone input.
    public func start(mixer mx: AudioMixer) throws {
        self.mixer = mx

        let inputNode = engine.inputNode
        let hwFormat = inputNode.outputFormat(forBus: 0)

        guard hwFormat.sampleRate > 0, hwFormat.channelCount > 0 else {
            fputs("MicCapture: no input device available\n", stderr)
            return
        }

        guard let converter = AVAudioConverter(from: hwFormat, to: targetFormat) else {
            fputs("MicCapture: could not create audio converter\n", stderr)
            return
        }

        inputNode.installTap(onBus: 0, bufferSize: 4096, format: hwFormat) {
            [weak self] (buffer, _) in
            guard let self, let mx = self.mixer else { return }

            let ratio = self.targetFormat.sampleRate / hwFormat.sampleRate
            let outputFrameCount = AVAudioFrameCount(Double(buffer.frameLength) * ratio)
            guard outputFrameCount > 0 else { return }

            guard let outputBuffer = AVAudioPCMBuffer(
                pcmFormat: self.targetFormat,
                frameCapacity: outputFrameCount
            ) else { return }

            var error: NSError?
            var inputConsumed = false
            converter.convert(to: outputBuffer, error: &error) { _, outStatus in
                if inputConsumed {
                    // Already provided input — tell converter there's no more data.
                    outStatus.pointee = .noDataNow
                    return nil
                }
                inputConsumed = true
                outStatus.pointee = .haveData
                return buffer
            }

            if let error {
                fputs("MicCapture: conversion error: \(error)\n", stderr)
                return
            }

            guard outputBuffer.frameLength > 0,
                  let int16Ptr = outputBuffer.int16ChannelData?[0] else { return }

            let byteCount = Int(outputBuffer.frameLength) * MemoryLayout<Int16>.size
            let data = Data(bytes: int16Ptr, count: byteCount)
            mx.addMicAudio(data)
        }

        engine.prepare()
        try engine.start()
        fputs("MicCapture: started (\(hwFormat.sampleRate) Hz → 16 kHz mono)\n", stderr)
    }

    public func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        fputs("MicCapture: stopped\n", stderr)
    }
}
