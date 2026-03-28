"""
Diagnostic: read raw PCM from the audio FIFO and save as WAV.

Usage:
    python tests/test_audio_capture.py

Reads 5 seconds of audio from /tmp/persuasion_audio.pipe, prints RMS levels,
and saves to /tmp/persuasion_audio_test.wav for manual playback verification.

The AudioCapture Swift binary must be running (start via Electron or manually).
"""

import math
import os
import struct
import sys
import wave

PIPE_PATH = "/tmp/persuasion_audio.pipe"
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # Int16
DURATION_S = 5
OUTPUT_WAV = "/tmp/persuasion_audio_test.wav"

EXPECTED_BYTES = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * DURATION_S


def main():
    if not os.path.exists(PIPE_PATH):
        print(f"FIFO not found at {PIPE_PATH}. Start AudioCapture first.", file=sys.stderr)
        sys.exit(1)

    print(f"Opening FIFO {PIPE_PATH} (will block until AudioCapture connects)...")
    fd = os.open(PIPE_PATH, os.O_RDONLY)
    print("FIFO opened. Reading audio...")

    collected = bytearray()
    chunk_count = 0
    while len(collected) < EXPECTED_BYTES:
        chunk = os.read(fd, 4096)
        if not chunk:
            print("EOF on FIFO (AudioCapture stopped).")
            break
        collected.extend(chunk)
        chunk_count += 1
        # Print progress every ~0.5s
        if chunk_count % 8 == 0:
            elapsed = len(collected) / (SAMPLE_RATE * SAMPLE_WIDTH)
            print(f"  {elapsed:.1f}s collected ({len(collected)} bytes, {chunk_count} chunks)")

    os.close(fd)

    if not collected:
        print("No audio data received.", file=sys.stderr)
        sys.exit(1)

    # Compute RMS
    num_samples = len(collected) // SAMPLE_WIDTH
    samples = struct.unpack(f"<{num_samples}h", collected[:num_samples * SAMPLE_WIDTH])

    rms = math.sqrt(sum(s * s for s in samples) / num_samples)
    peak = max(abs(s) for s in samples)
    zero_count = sum(1 for s in samples if s == 0)
    duration = num_samples / SAMPLE_RATE

    print(f"\nAudio stats:")
    print(f"  Duration:    {duration:.2f}s ({num_samples} samples)")
    print(f"  RMS level:   {rms:.1f} (of 32767 max)")
    print(f"  Peak level:  {peak} (of 32767 max)")
    print(f"  Zero samples: {zero_count} ({100*zero_count/num_samples:.1f}%)")
    print(f"  RMS dBFS:    {20*math.log10(rms/32767) if rms > 0 else -100:.1f} dB")

    if rms < 10:
        print("\n  WARNING: RMS is extremely low — audio may be silence or near-silence.")
    elif rms < 100:
        print("\n  WARNING: RMS is low — audio is very quiet.")
    else:
        print(f"\n  Audio levels look reasonable.")

    # Save as WAV
    with wave.open(OUTPUT_WAV, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(bytes(collected))

    print(f"\nSaved to {OUTPUT_WAV}")
    print(f"Play with: afplay {OUTPUT_WAV}")


if __name__ == "__main__":
    main()
