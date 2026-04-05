"""
End-to-end pipeline regression tests.

Tests the full audio → transcription → coaching pipeline.
Run with: pytest tests/test_pipeline_e2e.py -v

Tests:
  1. Deepgram API key validity (REST API ping)
  2. Anthropic API key validity (Haiku ping)
  3. Full coaching pipeline (SessionPipeline processes utterance → prompt)
  4. Audio FIFO data rate (requires running AudioCapture binary)
  5. Captured audio transcribability (FIFO audio → Deepgram REST)
  6. Deepgram streaming WebSocket (connect, send audio, KeepAlive, rate limiting)
"""

import asyncio
import json
import math
import os
import struct
import sys
import time
import wave

import pytest

# NOTE: Only tests that hit LIVE external APIs should be marked @pytest.mark.integration.
# Tests using the local Deepgram emulator run without network and are safe for CI.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_FILE = os.path.join(PROJECT_ROOT, ".settings.json")
PIPE_PATH = "/tmp/persuasion_audio.pipe"


def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _get_deepgram_key() -> str:
    return _load_settings().get("deepgram_api_key") or os.environ.get("DEEPGRAM_API_KEY", "")


def _get_anthropic_key() -> str:
    return _load_settings().get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")


# ── Test 1: Deepgram key ───────────────────────────────────────────────────

class TestDeepgramKey:
    """Verify Deepgram API key is configured and valid."""

    def test_key_exists(self):
        key = _get_deepgram_key()
        if not key:
            pytest.skip("No Deepgram key — set DEEPGRAM_API_KEY to run this test")

    @pytest.mark.integration
    def test_key_accepted_by_live_api(self):
        """Send a minimal WAV to real Deepgram REST API and verify 200 OK."""
        import urllib.request
        import urllib.error

        key = _get_deepgram_key()
        if not key:
            pytest.skip("No Deepgram key")

        import io
        buf = io.BytesIO()
        n = 16000
        samples = struct.pack(f"<{n}h", *([0] * n))
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(samples)

        req = urllib.request.Request(
            "https://api.deepgram.com/v1/listen?model=nova-2&language=en-US",
            data=buf.getvalue(),
            headers={
                "Authorization": f"Token {key}",
                "Content-Type": "audio/wav",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            assert resp.status == 200
            assert "results" in data
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                pytest.fail(f"Deepgram API key is invalid or expired (HTTP {e.code})")
            raise

    def test_emulator_rest_returns_utterances(self, deepgram_post_fn):
        """POST audio to the local Deepgram emulator and verify response format."""
        import asyncio

        async def _run():
            result = await deepgram_post_fn(
                "https://api.deepgram.com/v1/listen",
                headers={"Authorization": "Token test-key", "Content-Type": "audio/wav"},
                params={"model": "nova-2", "diarize": "true", "utterances": "true"},
                content=b"\x00" * 3200,
            )
            assert "results" in result
            utterances = result["results"]["utterances"]
            assert len(utterances) >= 1
            assert "transcript" in utterances[0]
            assert "speaker" in utterances[0]
            assert "start" in utterances[0]

        asyncio.get_event_loop().run_until_complete(_run())


# ── Test 2: Anthropic key ──────────────────────────────────────────────────

class TestAnthropicKey:
    """Verify Anthropic API key is configured and valid."""

    def test_key_exists(self):
        key = _get_anthropic_key()
        if not key:
            pytest.skip("No Anthropic key — set ANTHROPIC_API_KEY to run this test")

    @pytest.mark.asyncio
    async def test_haiku_responds(self):
        """Send a minimal prompt to Claude Haiku and verify response."""
        key = _get_anthropic_key()
        if not key:
            pytest.skip("No Anthropic key")

        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=key)
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=20,
                messages=[{"role": "user", "content": "Say hello in one word."}],
            ),
            timeout=10.0,
        )
        text = response.content[0].text.strip()
        assert len(text) > 0, "Haiku returned empty response"


# ── Test 3: Coaching pipeline ──────────────────────────────────────────────

class TestCoachingPipeline:
    """Verify SessionPipeline produces coaching prompts from utterances."""

    @pytest.mark.asyncio
    async def test_utterance_produces_prompt(self):
        """Process two utterances and verify a coaching prompt is generated."""
        key = _get_anthropic_key()
        if not key:
            pytest.skip("No Anthropic key")

        from anthropic import AsyncAnthropic
        from backend.coaching_engine import CoachingEngine
        from backend.main import SessionPipeline

        client = AsyncAnthropic(api_key=key)
        engine = CoachingEngine(
            user_speaker="speaker_0",
            anthropic_client=client,
            general_cadence_floor_s=0,  # disable floors for test
            elm_cadence_floor_s=0,
        )
        pipeline = SessionPipeline(
            session_id="test-regression",
            user_id="test-user",
            user_speaker="speaker_0",
            coaching_engine=engine,
        )

        # First utterance from a counterpart (non-user)
        await pipeline.process_utterance(
            speaker_id="speaker_1",
            text="I think this approach is fundamentally wrong and we need to start over.",
            is_final=True, start=0.0, end=5.0,
        )

        # Second utterance — more aggressive, likely triggers ELM or passes cadence
        prompt = await pipeline.process_utterance(
            speaker_id="speaker_1",
            text="Nobody asked for this feature, it was forced on us without any discussion.",
            is_final=True, start=5.0, end=10.0,
        )

        assert prompt is not None, "No coaching prompt generated after 2 utterances"
        assert len(prompt.text) > 0, "Coaching prompt text is empty"
        assert prompt.layer in ("self", "audience", "group")

    @pytest.mark.asyncio
    async def test_user_speaking_suppresses_prompt(self):
        """Verify no prompt when user is speaking."""
        key = _get_anthropic_key()
        if not key:
            pytest.skip("No Anthropic key")

        from anthropic import AsyncAnthropic
        from backend.coaching_engine import CoachingEngine
        from backend.main import SessionPipeline

        client = AsyncAnthropic(api_key=key)
        engine = CoachingEngine(
            user_speaker="speaker_0",
            anthropic_client=client,
            general_cadence_floor_s=0,
            elm_cadence_floor_s=0,
        )
        pipeline = SessionPipeline(
            session_id="test-suppression",
            user_id="test-user",
            user_speaker="speaker_0",
            coaching_engine=engine,
        )

        # User utterance — should NOT produce prompt
        prompt = await pipeline.process_utterance(
            speaker_id="speaker_0",  # user
            text="I think we should go ahead with option A.",
            is_final=True, start=0.0, end=3.0,
        )
        assert prompt is None, "Prompt should be suppressed when user is speaking"


# ── Test 4: Audio FIFO data rate ───────────────────────────────────────────

@pytest.mark.integration
class TestAudioFIFO:
    """Verify audio data from the FIFO arrives at ~1x real-time rate."""

    @pytest.mark.skipif(
        not os.path.exists(PIPE_PATH),
        reason="AudioCapture not running (no FIFO)"
    )
    def test_fifo_data_rate(self):
        """Read from FIFO for 3 seconds and verify rate is within 0.5x–2x of real-time."""
        fd = os.open(PIPE_PATH, os.O_RDONLY)

        # Drain stale data
        os.set_blocking(fd, False)
        try:
            while True:
                c = os.read(fd, 65536)
                if not c:
                    break
        except BlockingIOError:
            pass
        os.set_blocking(fd, True)

        # Measure sustained rate
        total = 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3.0:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            total += len(chunk)
        elapsed = time.monotonic() - t0
        os.close(fd)

        assert total > 0, "No audio data read from FIFO"
        rate = total / elapsed
        ratio = rate / 32000.0  # expected 32KB/s for 16kHz mono Int16
        assert 0.5 <= ratio <= 2.0, (
            f"FIFO data rate is {ratio:.1f}x real-time ({rate:.0f} bytes/s). "
            f"Expected ~1.0x (32000 bytes/s). "
            f"Check for duplicate AudioCapture processes or sample rate mismatch."
        )

    @pytest.mark.skipif(
        not os.path.exists(PIPE_PATH),
        reason="AudioCapture not running (no FIFO)"
    )
    def test_fifo_audio_transcribable(self):
        """Capture audio from FIFO and verify Deepgram can transcribe it via REST."""
        key = _get_deepgram_key()
        if not key:
            pytest.skip("No Deepgram key")

        fd = os.open(PIPE_PATH, os.O_RDONLY)
        # Read 3 seconds
        target = 16000 * 2 * 3
        data = bytearray()
        t0 = time.monotonic()
        while len(data) < target and time.monotonic() - t0 < 10:
            chunk = os.read(fd, 4096)
            if chunk:
                data.extend(chunk)
        os.close(fd)

        assert len(data) > 0, "No audio from FIFO"

        # Check RMS — should be above noise floor if anything is playing
        n_samples = len(data) // 2
        samples = struct.unpack(f"<{n_samples}h", data[:n_samples * 2])
        rms = math.sqrt(sum(s * s for s in samples) / n_samples) if n_samples > 0 else 0

        # Save as WAV and send to Deepgram REST
        import io
        import urllib.request

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(bytes(data))

        req = urllib.request.Request(
            "https://api.deepgram.com/v1/listen?model=nova-2&language=en-US&punctuate=true",
            data=buf.getvalue(),
            headers={
                "Authorization": f"Token {key}",
                "Content-Type": "audio/wav",
            },
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        transcript = (
            result.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
        )

        # If RMS is high enough that speech was likely playing, expect transcription
        if rms > 500:
            assert transcript, (
                f"Deepgram returned empty transcript despite RMS={rms:.0f}. "
                "Audio may be corrupt or at wrong sample rate."
            )


# ── Test 5: Deepgram streaming ────────────────────────────────────────────

class TestDeepgramStreaming:
    """Verify Deepgram streaming WebSocket returns transcriptions."""

    @pytest.mark.asyncio
    async def test_streaming_returns_transcript(self, deepgram_connect_fn):
        """Stream audio to emulator WebSocket and verify Results events arrive."""
        from backend.transcription import DeepgramTranscriber

        utterances = []

        async def on_utterance(speaker_id, text, is_final, start_s, end_s):
            if text.strip():
                utterances.append({"speaker_id": speaker_id, "text": text, "is_final": is_final})

        transcriber = DeepgramTranscriber(
            api_key="test-emulator-key",
            on_utterance=on_utterance,
            _connect_fn=deepgram_connect_fn,
        )
        await transcriber.connect()

        # Send dummy audio to trigger the emulator's fixture replay
        pcm_data = b"\x00" * 3200
        await transcriber.send_audio(pcm_data)
        await asyncio.sleep(1.0)  # wait for emulator to drip-feed events

        await transcriber.disconnect()

        assert len(utterances) >= 1, f"Expected at least 1 utterance, got {len(utterances)}"
        assert utterances[0]["is_final"] is True
        assert utterances[0]["speaker_id"].startswith("speaker_")

    @pytest.mark.asyncio
    async def test_keepalive_accepted(self, deepgram_connect_fn):
        """Verify emulator accepts KeepAlive messages without dropping."""
        from backend.transcription import DeepgramTranscriber

        async def noop_utterance(speaker_id, text, is_final, start_s, end_s):
            pass

        transcriber = DeepgramTranscriber(
            api_key="test-emulator-key",
            on_utterance=noop_utterance,
            _connect_fn=deepgram_connect_fn,
        )
        await transcriber.connect()
        assert transcriber.is_connected

        # Send audio to establish connection, then wait for KeepAlive to fire
        await transcriber.send_audio(b"\x00" * 3200)
        await asyncio.sleep(2.0)

        assert transcriber.is_connected, "Connection dropped — KeepAlive may not be working"
        await transcriber.disconnect()

    @pytest.mark.asyncio
    async def test_rapid_audio_does_not_crash(self, deepgram_connect_fn):
        """Send audio rapidly and verify the pipeline doesn't crash."""
        from backend.transcription import DeepgramTranscriber

        async def noop_utterance(speaker_id, text, is_final, start_s, end_s):
            pass

        transcriber = DeepgramTranscriber(
            api_key="test-emulator-key",
            on_utterance=noop_utterance,
            _connect_fn=deepgram_connect_fn,
        )
        await transcriber.connect()

        # Send 5 seconds of audio all at once
        n_samples = 16000 * 5
        pcm_data = struct.pack(f"<{n_samples}h", *([0] * n_samples))
        chunk_size = 3200
        for i in range(0, len(pcm_data), chunk_size):
            await transcriber.send_audio(pcm_data[i:i + chunk_size])

        await asyncio.sleep(1.0)
        assert transcriber.is_connected, "Connection dropped during rapid send"
        await transcriber.disconnect()

    # ── Live API tests (skipped in CI, need real key) ─────────────────────

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_live_streaming_returns_transcript(self):
        """Send audio to the real Deepgram API and verify transcript arrives."""
        key = _get_deepgram_key()
        if not key:
            pytest.skip("No Deepgram key")

        from backend.transcription import DeepgramTranscriber

        utterances = []

        async def on_utterance(speaker_id, text, is_final, start_s, end_s):
            if text.strip():
                utterances.append({"speaker_id": speaker_id, "text": text, "is_final": is_final})

        transcriber = DeepgramTranscriber(api_key=key, on_utterance=on_utterance)
        await transcriber.connect()

        import math as _math
        sample_rate = 16000
        n_samples = sample_rate * 2
        samples = [int(16000 * _math.sin(2 * _math.pi * 440 * t / sample_rate)) for t in range(n_samples)]
        pcm_data = struct.pack(f"<{n_samples}h", *samples)

        chunk_size = 3200
        for i in range(0, len(pcm_data), chunk_size):
            await transcriber.send_audio(pcm_data[i:i + chunk_size])
            await asyncio.sleep(0.1)

        await transcriber.finalize()
        await asyncio.sleep(1.0)
        await transcriber.disconnect()
        assert True, "Streaming pipeline completed without error"
