"""
Diagnostic: test Deepgram streaming with LIVE audio from the FIFO,
draining any stale buffered data first.

Usage:
    python tests/test_fifo_streaming.py

Requires AudioCapture binary running and audio playing (YouTube, Zoom, etc).
"""

import asyncio
import json
import math
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

PIPE = "/tmp/persuasion_audio.pipe"
SETTINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".settings.json")


def load_key():
    try:
        with open(SETTINGS) as f:
            return json.load(f).get("deepgram_api_key", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return os.environ.get("DEEPGRAM_API_KEY", "")


async def main():
    import websockets

    key = load_key()
    if not key:
        print("No Deepgram key found")
        sys.exit(1)

    if not os.path.exists(PIPE):
        print(f"FIFO not found at {PIPE}. Start AudioCapture first.")
        sys.exit(1)

    fd = os.open(PIPE, os.O_RDONLY)
    print("FIFO opened")

    # Phase 1: drain stale buffered data
    stale_bytes = 0
    print("Draining stale buffer...")
    os.set_blocking(fd, False)
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            stale_bytes += len(chunk)
    except BlockingIOError:
        pass
    os.set_blocking(fd, True)
    print(f"Drained {stale_bytes} bytes ({stale_bytes/32000:.1f}s) of stale data")

    # Phase 2: connect Deepgram and send LIVE audio
    url = (
        "wss://api.deepgram.com/v1/listen?"
        "encoding=linear16&sample_rate=16000&channels=1"
        "&language=en-US&model=nova-2&diarize=true"
        "&punctuate=true&interim_results=true"
        "&utterance_end_ms=1000&vad_events=true"
    )
    headers = {"Authorization": f"Token {key}"}

    ws = await websockets.connect(url, additional_headers=headers)
    print("Deepgram connected — sending live audio for 10s")
    print(">>> PLAY SOMETHING WITH SPEECH NOW <<<")

    results = []

    async def recv():
        try:
            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type", "")
                if t == "Results":
                    alt = msg.get("channel", {}).get("alternatives", [{}])[0]
                    transcript = alt.get("transcript", "")
                    is_final = msg.get("is_final", False)
                    results.append({"transcript": transcript, "final": is_final})
                    if transcript:
                        print(f'  >>> "{transcript}" (final={is_final})')
                elif t == "SpeechStarted":
                    print("  SpeechStarted")
                elif t == "UtteranceEnd":
                    print("  UtteranceEnd")
        except Exception:
            pass

    recv_task = asyncio.ensure_future(recv())

    loop = asyncio.get_event_loop()
    chunks = 0
    bytes_sent = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < 10.0:
        chunk = await loop.run_in_executor(None, lambda: os.read(fd, 4096))
        if not chunk:
            break
        await ws.send(chunk)
        chunks += 1
        bytes_sent += len(chunk)

        if chunks <= 3 or chunks % 50 == 0:
            n = len(chunk) // 2
            if n > 0:
                samples = struct.unpack(f"<{n}h", chunk[:n * 2])
                rms = math.sqrt(sum(s * s for s in samples) / n)
                print(f"  chunk {chunks}: {len(chunk)} bytes, RMS={rms:.0f}")

    elapsed = time.monotonic() - t0
    print(f"\nSent {chunks} chunks ({bytes_sent} bytes) in {elapsed:.1f}s")
    print(f"Audio duration: {bytes_sent/32000:.1f}s (rate: {bytes_sent/32000/elapsed:.1f}x realtime)")

    print("Waiting 3s for final results...")
    await asyncio.sleep(3.0)

    os.close(fd)
    await ws.close()
    recv_task.cancel()

    non_empty = [r for r in results if r["transcript"]]
    print(f"\nTotal results: {len(results)}, with transcript: {len(non_empty)}")
    if non_empty:
        print("SUCCESS: Deepgram streaming transcribes FIFO audio")
    else:
        print("FAIL: No transcripts from streaming")


if __name__ == "__main__":
    asyncio.run(main())
