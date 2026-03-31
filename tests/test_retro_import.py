"""
Tests for backend/retro_import.py (RetroImporter).

No real HTTP calls — the Deepgram REST endpoint is replaced by an injectable
``_post_fn`` that returns preset JSON payloads.

Covers:
  - process_file(): utterances delivered from the ``utterances`` block
  - process_file(): fallback path via channel alternatives (no utterances block)
  - Empty / blank transcripts are skipped
  - Speaker IDs are correctly mapped ("speaker_N")
  - start_s / end_s values are passed through correctly
  - is_final is always True for every delivered utterance
  - FileNotFoundError raised for missing file
  - RuntimeError raised when Deepgram returns an error payload
  - process_file() returns the correct utterance count
  - Multi-speaker response delivers utterances in order
  - on_progress fires after each non-empty utterance with (delivered, total)
  - on_progress total excludes blank/empty utterances
  - on_progress fires on channel fallback path too
  - cancel_event stops delivery before the next utterance
  - cancel_event set before delivery starts → 0 delivered
  - cancel_event=None delivers everything (no regression)
  - channel fallback also respects cancel_event
  - _content_type_for: .wav → audio/wav, .mp3 → audio/mpeg, unknown → audio/wav
  - _speaker_from_words: empty list, single speaker, multi-speaker majority vote,
    tie-breaking (lower speaker wins), no speaker key in words
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.retro_import import (
    RetroImporter,
    _content_type_for,
    _speaker_from_words,
    is_text_transcript,
    parse_text_transcript,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_audio_file(suffix: str = ".wav") -> str:
    """Create a tiny temp audio file and return its path."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(b"\x00" * 64)   # minimal dummy bytes
        return f.name


def _make_importer(
    post_response: dict,
    utterances_out: list | None = None,
) -> RetroImporter:
    """Build a RetroImporter whose HTTP POST returns *post_response*."""
    received: list = utterances_out if utterances_out is not None else []

    async def on_utterance(speaker_id, text, is_final, start_s, end_s) -> None:
        received.append({
            "speaker_id": speaker_id,
            "text": text,
            "is_final": is_final,
            "start_s": start_s,
            "end_s": end_s,
        })

    async def fake_post(url, *, headers, params, content):
        return post_response

    return RetroImporter(
        api_key="test-key",
        on_utterance=on_utterance,
        _post_fn=fake_post,
    ), received


# ---------------------------------------------------------------------------
# Fixtures / shared payloads
# ---------------------------------------------------------------------------

UTTERANCES_RESPONSE = {
    "results": {
        "utterances": [
            {
                "start": 0.0,
                "end": 2.5,
                "transcript": "Hello world.",
                "speaker": 0,
                "words": [],
            },
            {
                "start": 3.0,
                "end": 5.0,
                "transcript": "How are you?",
                "speaker": 1,
                "words": [],
            },
        ]
    }
}

CHANNELS_RESPONSE = {
    "results": {
        "channels": [
            {
                "alternatives": [
                    {
                        "transcript": "Channel fallback text.",
                        "words": [
                            {"word": "Channel", "start": 0.1, "end": 0.5, "speaker": 2},
                            {"word": "fallback", "start": 0.6, "end": 1.0, "speaker": 2},
                            {"word": "text.", "start": 1.1, "end": 1.4, "speaker": 2},
                        ],
                    }
                ]
            }
        ]
    }
}


# ---------------------------------------------------------------------------
# process_file — utterances path
# ---------------------------------------------------------------------------

class TestProcessFileUtterances:
    @pytest.mark.asyncio
    async def test_returns_utterance_count(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(UTTERANCES_RESPONSE)
            count = await importer.process_file(audio)
            assert count == 2
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_speaker_ids_from_utterances(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(UTTERANCES_RESPONSE)
            await importer.process_file(audio)
            assert received[0]["speaker_id"] == "speaker_0"
            assert received[1]["speaker_id"] == "speaker_1"
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_transcripts_from_utterances(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(UTTERANCES_RESPONSE)
            await importer.process_file(audio)
            assert received[0]["text"] == "Hello world."
            assert received[1]["text"] == "How are you?"
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_timings_from_utterances(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(UTTERANCES_RESPONSE)
            await importer.process_file(audio)
            assert received[0]["start_s"] == 0.0
            assert received[0]["end_s"] == 2.5
            assert received[1]["start_s"] == 3.0
            assert received[1]["end_s"] == 5.0
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_is_final_always_true(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(UTTERANCES_RESPONSE)
            await importer.process_file(audio)
            assert all(u["is_final"] is True for u in received)
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_utterances_delivered_in_order(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(UTTERANCES_RESPONSE)
            await importer.process_file(audio)
            texts = [u["text"] for u in received]
            assert texts == ["Hello world.", "How are you?"]
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_blank_utterances_skipped(self):
        response = {
            "results": {
                "utterances": [
                    {"start": 0.0, "end": 1.0, "transcript": "   ", "speaker": 0},
                    {"start": 1.0, "end": 2.0, "transcript": "Real text.", "speaker": 0},
                    {"start": 2.0, "end": 3.0, "transcript": "", "speaker": 1},
                ]
            }
        }
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(response)
            count = await importer.process_file(audio)
            assert count == 1
            assert received[0]["text"] == "Real text."
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_empty_utterances_list_falls_back_to_channels(self):
        """Utterances key present but empty → fall through to channels."""
        response = {
            "results": {
                "utterances": [],
                "channels": [
                    {
                        "alternatives": [
                            {
                                "transcript": "From channel.",
                                "words": [{"word": "From", "start": 0.0, "end": 0.3, "speaker": 0}],
                            }
                        ]
                    }
                ],
            }
        }
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(response)
            count = await importer.process_file(audio)
            assert count == 1
            assert received[0]["text"] == "From channel."
        finally:
            os.unlink(audio)


# ---------------------------------------------------------------------------
# process_file — channel fallback path
# ---------------------------------------------------------------------------

class TestProcessFileChannelFallback:
    @pytest.mark.asyncio
    async def test_returns_utterance_count(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(CHANNELS_RESPONSE)
            count = await importer.process_file(audio)
            assert count == 1
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_speaker_from_words(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(CHANNELS_RESPONSE)
            await importer.process_file(audio)
            assert received[0]["speaker_id"] == "speaker_2"
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_timings_from_words(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(CHANNELS_RESPONSE)
            await importer.process_file(audio)
            assert received[0]["start_s"] == pytest.approx(0.1)
            assert received[0]["end_s"] == pytest.approx(1.4)
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_transcript_from_alternative(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(CHANNELS_RESPONSE)
            await importer.process_file(audio)
            assert received[0]["text"] == "Channel fallback text."
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_blank_channel_transcript_skipped(self):
        response = {
            "results": {
                "channels": [
                    {"alternatives": [{"transcript": "  ", "words": []}]},
                    {"alternatives": [{"transcript": "Good text.", "words": []}]},
                ]
            }
        }
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(response)
            count = await importer.process_file(audio)
            assert count == 1
            assert received[0]["text"] == "Good text."
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_channel_with_no_alternatives_skipped(self):
        response = {
            "results": {
                "channels": [
                    {"alternatives": []},
                    {"alternatives": [{"transcript": "Text.", "words": []}]},
                ]
            }
        }
        audio = _make_audio_file()
        try:
            importer, received = _make_importer(response)
            count = await importer.process_file(audio)
            assert count == 1
        finally:
            os.unlink(audio)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_missing_file_raises_file_not_found(self):
        importer, _ = _make_importer({})
        with pytest.raises(FileNotFoundError):
            await importer.process_file("/nonexistent/path/audio.wav")

    @pytest.mark.asyncio
    async def test_deepgram_error_payload_raises_runtime_error(self):
        error_response = {"error": "INVALID_AUTH", "message": "Bad API key"}
        audio = _make_audio_file()
        try:
            importer, _ = _make_importer(error_response)
            with pytest.raises(RuntimeError, match="Deepgram error"):
                await importer.process_file(audio)
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_empty_results_returns_zero(self):
        audio = _make_audio_file()
        try:
            importer, received = _make_importer({"results": {}})
            count = await importer.process_file(audio)
            assert count == 0
            assert received == []
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_post_fn_receives_api_key_header(self):
        """The Authorization header must include the API key."""
        captured: dict = {}

        async def spy_post(url, *, headers, params, content):
            captured["headers"] = headers
            return {"results": {}}

        async def on_utterance(*_): pass

        importer = RetroImporter(
            api_key="my-secret-key",
            on_utterance=on_utterance,
            _post_fn=spy_post,
        )
        audio = _make_audio_file()
        try:
            await importer.process_file(audio)
        finally:
            os.unlink(audio)

        assert captured["headers"]["Authorization"] == "Token my-secret-key"

    @pytest.mark.asyncio
    async def test_post_fn_receives_correct_url(self):
        captured: dict = {}

        async def spy_post(url, *, headers, params, content):
            captured["url"] = url
            return {"results": {}}

        async def on_utterance(*_): pass

        importer = RetroImporter(
            api_key="key",
            on_utterance=on_utterance,
            _post_fn=spy_post,
        )
        audio = _make_audio_file()
        try:
            await importer.process_file(audio)
        finally:
            os.unlink(audio)

        assert "deepgram.com" in captured["url"]

    @pytest.mark.asyncio
    async def test_post_fn_receives_audio_bytes(self):
        """The POST body must be the file's raw bytes."""
        captured: dict = {}

        async def spy_post(url, *, headers, params, content):
            captured["content"] = content
            return {"results": {}}

        async def on_utterance(*_): pass

        importer = RetroImporter(
            api_key="key",
            on_utterance=on_utterance,
            _post_fn=spy_post,
        )
        audio = _make_audio_file()
        try:
            file_bytes = Path(audio).read_bytes()
            await importer.process_file(audio)
        finally:
            os.unlink(audio)

        assert captured["content"] == file_bytes


# ---------------------------------------------------------------------------
# _content_type_for
# ---------------------------------------------------------------------------

class TestContentTypeFor:
    def test_wav(self):
        assert _content_type_for(Path("audio.wav")) == "audio/wav"

    def test_mp3(self):
        assert _content_type_for(Path("audio.mp3")) == "audio/mpeg"

    def test_unknown_extension_defaults_to_wav(self):
        assert _content_type_for(Path("audio.pcm")) == "audio/wav"

    def test_no_extension_defaults_to_wav(self):
        assert _content_type_for(Path("audiofile")) == "audio/wav"


# ---------------------------------------------------------------------------
# _speaker_from_words
# ---------------------------------------------------------------------------

class TestSpeakerFromWords:
    def test_empty_list_returns_speaker_0(self):
        assert _speaker_from_words([]) == "speaker_0"

    def test_single_speaker(self):
        words = [{"word": "hi", "speaker": 3}]
        assert _speaker_from_words(words) == "speaker_3"

    def test_majority_vote(self):
        words = [
            {"word": "a", "speaker": 0},
            {"word": "b", "speaker": 1},
            {"word": "c", "speaker": 1},
        ]
        assert _speaker_from_words(words) == "speaker_1"

    def test_tie_goes_to_lower_speaker_id(self):
        words = [
            {"word": "a", "speaker": 2},
            {"word": "b", "speaker": 0},
        ]
        assert _speaker_from_words(words) == "speaker_0"

    def test_words_without_speaker_key_ignored(self):
        words = [
            {"word": "no-speaker"},
            {"word": "has-speaker", "speaker": 5},
        ]
        assert _speaker_from_words(words) == "speaker_5"

    def test_all_words_without_speaker_key_returns_speaker_0(self):
        words = [{"word": "a"}, {"word": "b"}]
        assert _speaker_from_words(words) == "speaker_0"


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------

def _make_importer_with_progress(post_response: dict):
    """Build a RetroImporter whose HTTP POST returns *post_response*.

    Returns (importer, received_utterances, progress_calls).
    """
    received: list[dict] = []
    progress_calls: list[tuple[int, int]] = []

    async def on_utterance(speaker_id, text, is_final, start_s, end_s) -> None:
        received.append({"speaker_id": speaker_id, "text": text})

    async def on_progress(delivered: int, total: int) -> None:
        progress_calls.append((delivered, total))

    async def fake_post(url, *, headers, params, content):
        return post_response

    importer = RetroImporter(
        api_key="test-key",
        on_utterance=on_utterance,
        on_progress=on_progress,
        _post_fn=fake_post,
    )
    return importer, received, progress_calls


class TestProgressCallback:
    @pytest.mark.asyncio
    async def test_fires_after_each_utterance(self):
        response = {
            "results": {
                "utterances": [
                    {"transcript": "one", "speaker": 0, "start": 0.0, "end": 1.0},
                    {"transcript": "two", "speaker": 0, "start": 1.0, "end": 2.0},
                    {"transcript": "three", "speaker": 0, "start": 2.0, "end": 3.0},
                ]
            }
        }
        audio = _make_audio_file()
        try:
            imp, received, progress = _make_importer_with_progress(response)
            count = await imp.process_file(audio)
            assert count == 3
            assert progress == [(1, 3), (2, 3), (3, 3)]
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_total_excludes_blank_utterances(self):
        response = {
            "results": {
                "utterances": [
                    {"transcript": "", "speaker": 0, "start": 0.0, "end": 0.1},
                    {"transcript": "real", "speaker": 0, "start": 0.2, "end": 0.5},
                    {"transcript": "   ", "speaker": 0, "start": 0.5, "end": 0.6},
                ]
            }
        }
        audio = _make_audio_file()
        try:
            imp, received, progress = _make_importer_with_progress(response)
            await imp.process_file(audio)
            # Only 1 non-empty utterance → total == 1
            assert progress == [(1, 1)]
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_not_called_for_blank_utterances(self):
        """Progress must not fire for skipped utterances."""
        response = {
            "results": {
                "utterances": [
                    {"transcript": "  ", "speaker": 0, "start": 0.0, "end": 0.1},
                ]
            }
        }
        audio = _make_audio_file()
        try:
            imp, received, progress = _make_importer_with_progress(response)
            await imp.process_file(audio)
            assert progress == []
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_fires_on_channel_fallback_path(self):
        response = {
            "results": {
                "channels": [
                    {"alternatives": [{"transcript": "alpha", "words": []}]},
                    {"alternatives": [{"transcript": "beta", "words": []}]},
                ]
            }
        }
        audio = _make_audio_file()
        try:
            imp, received, progress = _make_importer_with_progress(response)
            count = await imp.process_file(audio)
            assert count == 2
            assert progress == [(1, 2), (2, 2)]
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_no_error_when_on_progress_not_set(self):
        """Default (on_progress=None) must not raise."""
        response = {
            "results": {
                "utterances": [
                    {"transcript": "hello", "speaker": 0, "start": 0.0, "end": 1.0},
                ]
            }
        }
        audio = _make_audio_file()
        try:
            importer, _ = _make_importer(response)
            # should not raise
            count = await importer.process_file(audio)
            assert count == 1
        finally:
            os.unlink(audio)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_event_stops_mid_delivery(self):
        """cancel_event set during on_utterance stops before the next utterance."""
        response = {
            "results": {
                "utterances": [
                    {"transcript": "one", "speaker": 0, "start": 0.0, "end": 1.0},
                    {"transcript": "two", "speaker": 0, "start": 1.0, "end": 2.0},
                    {"transcript": "three", "speaker": 0, "start": 2.0, "end": 3.0},
                ]
            }
        }
        cancel = asyncio.Event()
        received: list[str] = []

        async def on_utt(sid, text, is_final, start_s, end_s) -> None:
            received.append(text)
            if len(received) == 2:
                cancel.set()  # set after second utterance

        async def fake_post(url, *, headers, params, content):
            return response

        audio = _make_audio_file()
        try:
            imp = RetroImporter(api_key="x", on_utterance=on_utt, _post_fn=fake_post)
            count = await imp.process_file(audio, cancel_event=cancel)

            # cancel is checked BEFORE each utterance, so "three" is never delivered
            assert count == 2
            assert received == ["one", "two"]
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_cancel_event_set_before_delivery_delivers_nothing(self):
        response = {
            "results": {
                "utterances": [
                    {"transcript": "one", "speaker": 0, "start": 0.0, "end": 1.0},
                ]
            }
        }
        cancel = asyncio.Event()
        cancel.set()  # already set before we start

        received: list[str] = []

        async def on_utt(sid, text, *_) -> None:
            received.append(text)

        async def fake_post(url, *, headers, params, content):
            return response

        audio = _make_audio_file()
        try:
            imp = RetroImporter(api_key="x", on_utterance=on_utt, _post_fn=fake_post)
            count = await imp.process_file(audio, cancel_event=cancel)

            assert count == 0
            assert received == []
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_cancel_event_none_delivers_all(self):
        """Passing cancel_event=None (default) must not affect delivery."""
        response = {
            "results": {
                "utterances": [
                    {"transcript": "a", "speaker": 0, "start": 0.0, "end": 0.5},
                    {"transcript": "b", "speaker": 0, "start": 0.5, "end": 1.0},
                ]
            }
        }

        async def on_utt(*_) -> None: pass

        async def fake_post(url, *, headers, params, content):
            return response

        audio = _make_audio_file()
        try:
            imp = RetroImporter(api_key="x", on_utterance=on_utt, _post_fn=fake_post)
            count = await imp.process_file(audio, cancel_event=None)
            assert count == 2
        finally:
            os.unlink(audio)

    @pytest.mark.asyncio
    async def test_cancel_channel_fallback_path(self):
        """cancel_event is also respected in the channel fallback path."""
        response = {
            "results": {
                "channels": [
                    {"alternatives": [{"transcript": "alpha", "words": []}]},
                    {"alternatives": [{"transcript": "beta", "words": []}]},
                    {"alternatives": [{"transcript": "gamma", "words": []}]},
                ]
            }
        }
        cancel = asyncio.Event()
        received: list[str] = []

        async def on_utt(sid, text, *_) -> None:
            received.append(text)
            cancel.set()  # cancel after first

        async def fake_post(url, *, headers, params, content):
            return response

        audio = _make_audio_file()
        try:
            imp = RetroImporter(api_key="x", on_utterance=on_utt, _post_fn=fake_post)
            count = await imp.process_file(audio, cancel_event=cancel)

            assert count == 1
            assert received == ["alpha"]
        finally:
            os.unlink(audio)


# ---------------------------------------------------------------------------
# is_text_transcript
# ---------------------------------------------------------------------------

class TestIsTextTranscript:
    def test_txt_extension(self):
        assert is_text_transcript("meeting.txt") is True

    def test_json_extension(self):
        assert is_text_transcript("transcript.json") is True

    def test_jsonl_extension(self):
        assert is_text_transcript("data.jsonl") is True

    def test_md_extension(self):
        assert is_text_transcript("notes.md") is True

    def test_wav_is_not_text(self):
        assert is_text_transcript("audio.wav") is False

    def test_mp3_is_not_text(self):
        assert is_text_transcript("recording.mp3") is False

    def test_m4a_is_not_text(self):
        assert is_text_transcript("file.m4a") is False

    def test_uppercase_extension(self):
        assert is_text_transcript("FILE.TXT") is True

    def test_no_extension(self):
        assert is_text_transcript("noext") is False


# ---------------------------------------------------------------------------
# parse_text_transcript — plain text format
# ---------------------------------------------------------------------------

class TestParseTextTranscriptPlainText:
    def test_speaker_colon_format(self):
        text = "Alice: Hello everyone.\nBob: Thanks for joining."
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Alice"
        assert result[0]["text"] == "Hello everyone."
        assert result[1]["speaker_id"] == "Bob"
        assert result[1]["text"] == "Thanks for joining."

    def test_line_without_colon_uses_previous_speaker(self):
        text = "Alice: First line.\nContinuation without colon."
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[1]["speaker_id"] == "Alice"
        assert result[1]["text"] == "Continuation without colon."

    def test_first_line_without_colon_uses_speaker_0(self):
        text = "Just some text without a speaker."
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert result[0]["speaker_id"] == "speaker_0"

    def test_blank_lines_are_skipped(self):
        text = "Alice: Hello.\n\n\nBob: World."
        result = parse_text_transcript(text)
        assert len(result) == 2

    def test_empty_input_returns_empty(self):
        assert parse_text_transcript("") == []

    def test_whitespace_only_returns_empty(self):
        assert parse_text_transcript("   \n  \n  ") == []

    def test_start_end_default_to_zero(self):
        result = parse_text_transcript("Alice: Hello.")
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 0.0


# ---------------------------------------------------------------------------
# parse_text_transcript — Markdown bold format (**Name:** text)
# ---------------------------------------------------------------------------

class TestParseTextTranscriptMarkdown:
    def test_markdown_bold_speaker(self):
        text = "**Mark:** Hello everyone.\n**Sarah:** Thanks for joining."
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Mark"
        assert result[0]["text"] == "Hello everyone."
        assert result[1]["speaker_id"] == "Sarah"
        assert result[1]["text"] == "Thanks for joining."

    def test_markdown_with_header_and_metadata_skipped(self):
        text = (
            "# Meeting Notes\n"
            "**Date:** March 26, 2026\n"
            "**Participants:** Alice, Bob\n"
            "---\n"
            "**Alice:** First point.\n"
            "**Bob:** Second point.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Alice"
        assert result[1]["speaker_id"] == "Bob"

    def test_markdown_mixed_with_plain(self):
        text = "**Alice:** Markdown style.\nBob: Plain style."
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Alice"
        assert result[1]["speaker_id"] == "Bob"

    def test_markdown_four_speakers(self):
        text = (
            "**Vish:** Let me set the context.\n"
            "**Mark:** Sounds good.\n"
            "**Francisco:** Here's the technical plan.\n"
            "**Jared:** I agree with that approach.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 4
        speakers = {r["speaker_id"] for r in result}
        assert speakers == {"Vish", "Mark", "Francisco", "Jared"}

    def test_continuation_line_uses_previous_markdown_speaker(self):
        text = "**Alice:** First line.\nContinuation without speaker."
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[1]["speaker_id"] == "Alice"

    def test_real_transcript_format(self):
        """Full format matching the user's actual uploaded transcript."""
        text = (
            "# Sailplane–Dell Cumulus Integration Kickoff\n"
            "**Date:** March 26, 2026\n"
            "**Participants:** Vish (COO), Francisco (CTO), Mark (Dell), Jared (Dell)\n"
            "---\n"
            "**Mark:** So which time zone are you in, Francisco?\n"
            "**Vish:** Hey, Jared.\n"
            "**Jared:** I'm not on my deathbed or anything.\n"
            "**Francisco:** Let me walk through the integration plan.\n"
            "**Vish:** Great. Well, I know this is our kickoff.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 5
        speakers = {r["speaker_id"] for r in result}
        assert speakers == {"Mark", "Vish", "Jared", "Francisco"}
        # No metadata lines should appear
        for r in result:
            assert not r["text"].startswith("#")
            assert not r["text"].startswith("**Date")
            assert not r["text"].startswith("---")


# ---------------------------------------------------------------------------
# parse_text_transcript — JSON format
# ---------------------------------------------------------------------------

class TestParseTextTranscriptJSON:
    def test_json_array_with_speaker_id(self):
        text = '[{"speaker_id": "Alice", "text": "Hello.", "start": 0.0, "end": 1.5}]'
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert result[0]["speaker_id"] == "Alice"
        assert result[0]["text"] == "Hello."
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 1.5

    def test_json_array_with_speaker_key(self):
        text = '[{"speaker": "Bob", "text": "Hi."}]'
        result = parse_text_transcript(text)
        assert result[0]["speaker_id"] == "Bob"

    def test_integer_speaker_mapped_to_string(self):
        text = '[{"speaker": 2, "text": "Hello."}]'
        result = parse_text_transcript(text)
        assert result[0]["speaker_id"] == "speaker_2"

    def test_missing_speaker_defaults_to_speaker_0(self):
        text = '[{"text": "No speaker."}]'
        result = parse_text_transcript(text)
        assert result[0]["speaker_id"] == "speaker_0"

    def test_transcript_key_also_accepted(self):
        """Deepgram-style 'transcript' key instead of 'text'."""
        text = '[{"speaker": 0, "transcript": "From Deepgram."}]'
        result = parse_text_transcript(text)
        assert result[0]["text"] == "From Deepgram."

    def test_blank_text_entries_skipped(self):
        text = '[{"speaker": 0, "text": ""}, {"speaker": 0, "text": "Real."}]'
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert result[0]["text"] == "Real."

    def test_deepgram_wrapper_format(self):
        text = '{"results": {"utterances": [{"speaker": 0, "transcript": "Wrapped.", "start": 1.0, "end": 2.0}]}}'
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert result[0]["text"] == "Wrapped."
        assert result[0]["start"] == 1.0

    def test_start_end_default_when_absent(self):
        text = '[{"speaker_id": "A", "text": "No timing."}]'
        result = parse_text_transcript(text)
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 0.0

    def test_invalid_json_falls_through_to_plain_text(self):
        text = '{"broken json'
        result = parse_text_transcript(text)
        # Falls through to plain-text parsing; one line, no colon → speaker_0
        assert len(result) == 1
        assert result[0]["speaker_id"] == "speaker_0"


# ---------------------------------------------------------------------------
# parse_text_transcript — WebVTT format (.vtt)
# ---------------------------------------------------------------------------

class TestParseVTT:
    def test_vtt_with_voice_tags(self):
        text = (
            "WEBVTT\n\n"
            "1\n00:00:03.000 --> 00:00:07.000\n<v Sarah>Hello everyone.\n\n"
            "2\n00:00:07.000 --> 00:00:15.000\n<v Michael>Thanks for having me.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Sarah"
        assert result[0]["text"] == "Hello everyone."
        assert result[1]["speaker_id"] == "Michael"
        assert result[1]["text"] == "Thanks for having me."

    def test_vtt_without_voice_tags(self):
        text = (
            "WEBVTT\n\n"
            "00:00:03.000 --> 00:00:07.000\nHello everyone.\n\n"
            "00:00:07.000 --> 00:00:15.000\nThanks for having me.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "speaker_0"
        assert result[1]["speaker_id"] == "speaker_0"

    def test_vtt_multi_line_cue(self):
        text = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:05.000\n"
            "<v Alice>This is the first line.\n"
            "And this is the second line.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert result[0]["speaker_id"] == "Alice"
        assert "first line" in result[0]["text"]
        assert "second line" in result[0]["text"]

    def test_vtt_closing_voice_tag_stripped(self):
        text = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n"
            "<v Sarah>Hello everyone.</v>\n"
        )
        result = parse_text_transcript(text)
        assert result[0]["text"] == "Hello everyone."
        assert "</v>" not in result[0]["text"]

    def test_vtt_timestamps_extracted(self):
        text = (
            "WEBVTT\n\n"
            "00:01:23.456 --> 00:01:30.789\n"
            "<v Alice>Testing timestamps.\n"
        )
        result = parse_text_transcript(text)
        assert abs(result[0]["start"] - 83.456) < 0.001
        assert abs(result[0]["end"] - 90.789) < 0.001

    def test_vtt_note_blocks_skipped(self):
        text = (
            "WEBVTT\n\n"
            "NOTE\nThis is a comment block.\n\n"
            "00:00:01.000 --> 00:00:03.000\n"
            "<v Alice>Actual speech.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert result[0]["text"] == "Actual speech."

    def test_vtt_empty_cue_skipped(self):
        text = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n\n\n"
            "00:00:03.000 --> 00:00:05.000\n"
            "<v Bob>Real text.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert result[0]["speaker_id"] == "Bob"

    def test_vtt_with_bom(self):
        text = (
            "\ufeffWEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n"
            "<v Alice>BOM test.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert result[0]["speaker_id"] == "Alice"


# ---------------------------------------------------------------------------
# parse_text_transcript — SRT format (.srt)
# ---------------------------------------------------------------------------

class TestParseSRT:
    def test_srt_with_speaker_colon(self):
        text = (
            "1\n00:00:01,000 --> 00:00:04,000\nSpeaker A: Hello.\n\n"
            "2\n00:00:05,000 --> 00:00:07,000\nSpeaker B: Hi there.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Speaker A"
        assert result[0]["text"] == "Hello."
        assert result[1]["speaker_id"] == "Speaker B"

    def test_srt_with_dash_prefix(self):
        text = (
            "1\n00:00:01,000 --> 00:00:04,000\n- Sarah: Hello everyone.\n\n"
            "2\n00:00:05,000 --> 00:00:07,000\n- Michael: Thanks.\n"
        )
        result = parse_text_transcript(text)
        assert result[0]["speaker_id"] == "Sarah"
        assert result[1]["speaker_id"] == "Michael"

    def test_srt_without_speaker(self):
        text = (
            "1\n00:00:01,000 --> 00:00:04,000\nHello everyone.\n\n"
            "2\n00:00:05,000 --> 00:00:07,000\nThanks.\n"
        )
        result = parse_text_transcript(text)
        assert result[0]["speaker_id"] == "speaker_0"
        assert result[1]["speaker_id"] == "speaker_0"

    def test_srt_multi_line_text(self):
        text = (
            "1\n00:00:01,000 --> 00:00:04,000\n"
            "Sarah: This is the first line.\n"
            "And this continues.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 1
        assert "first line" in result[0]["text"]
        assert "continues" in result[0]["text"]

    def test_srt_timestamps_extracted(self):
        text = "1\n00:01:30,500 --> 00:01:35,250\nHello.\n"
        result = parse_text_transcript(text)
        assert abs(result[0]["start"] - 90.5) < 0.001
        assert abs(result[0]["end"] - 95.25) < 0.001

    def test_srt_malformed_block_skipped(self):
        text = (
            "1\n00:00:01,000 --> 00:00:04,000\nReal text.\n\n"
            "2\n00:00:05,000 --> 00:00:07,000\n\n\n"
            "3\n00:00:08,000 --> 00:00:10,000\nMore text.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# parse_text_transcript — Teams inline VTT
# ---------------------------------------------------------------------------

class TestParseTeamsInlineVTT:
    def test_teams_inline_basic(self):
        text = (
            '00:03:02.870 --> 00:03:05.570 <v Sean Landy>So now we get its goal.\n'
            '00:03:06.100 --> 00:03:09.200 <v Speaker 2>I agree with that.\n'
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Sean Landy"
        assert result[0]["text"] == "So now we get its goal."
        assert result[1]["speaker_id"] == "Speaker 2"

    def test_teams_inline_closing_tag(self):
        text = '00:03:02.870 --> 00:03:05.570 <v Alice>Hello.</v>\n'
        result = parse_text_transcript(text)
        assert result[0]["text"] == "Hello."
        assert "</v>" not in result[0]["text"]

    def test_teams_inline_multiple_speakers(self):
        text = (
            '00:00:01.000 --> 00:00:03.000 <v Alice>First.\n'
            '00:00:03.000 --> 00:00:05.000 <v Bob>Second.\n'
            '00:00:05.000 --> 00:00:07.000 <v Alice>Third.\n'
        )
        result = parse_text_transcript(text)
        assert len(result) == 3
        speakers = [r["speaker_id"] for r in result]
        assert speakers == ["Alice", "Bob", "Alice"]

    def test_teams_inline_timestamps_extracted(self):
        text = '00:01:23.456 --> 00:01:30.789 <v Alice>Test.\n'
        result = parse_text_transcript(text)
        assert abs(result[0]["start"] - 83.456) < 0.001
        assert abs(result[0]["end"] - 90.789) < 0.001


# ---------------------------------------------------------------------------
# parse_text_transcript — Google Meet format
# ---------------------------------------------------------------------------

class TestParseGoogleMeet:
    def test_google_meet_basic(self):
        text = (
            "Sarah Chen (00:01:23)\n"
            "Hello everyone, welcome to the meeting.\n\n"
            "Michael Park (00:01:45)\n"
            "Thanks for having me.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Sarah Chen"
        assert result[0]["text"] == "Hello everyone, welcome to the meeting."
        assert result[1]["speaker_id"] == "Michael Park"

    def test_google_meet_multi_line_text(self):
        text = (
            "Alice (00:00:01)\n"
            "This is the first line.\n"
            "And this is a continuation.\n\n"
            "Bob (00:00:10)\n"
            "Got it.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert "first line" in result[0]["text"]
        assert "continuation" in result[0]["text"]

    def test_google_meet_short_timestamp(self):
        text = "Alice (01:23)\nHello.\n"
        result = parse_text_transcript(text)
        assert result[0]["speaker_id"] == "Alice"
        assert abs(result[0]["start"] - 83.0) < 0.001

    def test_google_meet_name_with_spaces(self):
        text = "Dr. Jane Smith (00:05:30)\nGood morning everyone.\n"
        result = parse_text_transcript(text)
        assert result[0]["speaker_id"] == "Dr. Jane Smith"


# ---------------------------------------------------------------------------
# parse_text_transcript — Zoom TXT formats
# ---------------------------------------------------------------------------

class TestParseZoomTXT:
    def test_zoom_bracket_format(self):
        text = (
            "Sarah: [00:00:03] Hello everyone.\n"
            "Michael: [00:00:07] Thanks for joining.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Sarah"
        assert result[0]["text"] == "Hello everyone."
        assert result[1]["speaker_id"] == "Michael"

    def test_zoom_bracket_timestamps_extracted(self):
        text = "Sarah: [00:01:30] Testing timestamps.\n"
        result = parse_text_transcript(text)
        assert abs(result[0]["start"] - 90.0) < 0.001

    def test_zoom_leading_timestamp(self):
        text = (
            "00:00:03 Sarah: Hello everyone.\n"
            "00:00:07 Michael: Thanks for joining.\n"
        )
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Sarah"
        assert result[1]["speaker_id"] == "Michael"

    def test_zoom_leading_timestamps_extracted(self):
        text = "00:01:30 Sarah: Testing timestamps.\n"
        result = parse_text_transcript(text)
        assert abs(result[0]["start"] - 90.0) < 0.001


# ---------------------------------------------------------------------------
# parse_text_transcript — BOM handling
# ---------------------------------------------------------------------------

class TestBOMHandling:
    def test_bom_prefix_stripped(self):
        text = "\ufeffAlice: Hello.\nBob: Hi."
        result = parse_text_transcript(text)
        assert len(result) == 2
        assert result[0]["speaker_id"] == "Alice"


# ---------------------------------------------------------------------------
# is_text_transcript — new extensions
# ---------------------------------------------------------------------------

class TestIsTextTranscriptNewExtensions:
    def test_vtt_extension(self):
        assert is_text_transcript("meeting.vtt") is True

    def test_srt_extension(self):
        assert is_text_transcript("subtitles.srt") is True

    def test_vtt_uppercase(self):
        assert is_text_transcript("MEETING.VTT") is True

    def test_srt_uppercase(self):
        assert is_text_transcript("SUBTITLES.SRT") is True


# ---------------------------------------------------------------------------
# process_utterances (pre-parsed, no Deepgram)
# ---------------------------------------------------------------------------

class TestProcessUtterances:
    @pytest.mark.asyncio
    async def test_delivers_all_utterances(self):
        received: list[dict] = []

        async def on_utt(sid, text, is_final, start_s, end_s):
            received.append({"speaker_id": sid, "text": text, "start": start_s, "end": end_s})

        imp = RetroImporter(api_key="x", on_utterance=on_utt, _post_fn=AsyncMock())
        utts = [
            {"speaker_id": "Alice", "text": "Hello.", "start": 0.0, "end": 1.0},
            {"speaker_id": "Bob", "text": "Hi.", "start": 1.5, "end": 2.0},
        ]
        count = await imp.process_utterances(utts)
        assert count == 2
        assert received[0]["speaker_id"] == "Alice"
        assert received[1]["speaker_id"] == "Bob"

    @pytest.mark.asyncio
    async def test_skips_empty_text(self):
        received: list[dict] = []

        async def on_utt(sid, text, is_final, start_s, end_s):
            received.append({"text": text})

        imp = RetroImporter(api_key="x", on_utterance=on_utt, _post_fn=AsyncMock())
        utts = [
            {"speaker_id": "A", "text": "   "},
            {"speaker_id": "A", "text": "Real."},
            {"speaker_id": "A", "text": ""},
        ]
        count = await imp.process_utterances(utts)
        assert count == 1
        assert received[0]["text"] == "Real."

    @pytest.mark.asyncio
    async def test_progress_callback_fires(self):
        progress: list[tuple[int, int]] = []

        async def on_utt(*_): pass
        async def on_prog(d, t): progress.append((d, t))

        imp = RetroImporter(api_key="x", on_utterance=on_utt, on_progress=on_prog, _post_fn=AsyncMock())
        utts = [
            {"speaker_id": "A", "text": "One."},
            {"speaker_id": "B", "text": "Two."},
        ]
        await imp.process_utterances(utts)
        assert progress == [(1, 2), (2, 2)]

    @pytest.mark.asyncio
    async def test_cancel_event_stops_delivery(self):
        received: list[str] = []
        cancel = asyncio.Event()

        async def on_utt(sid, text, *_):
            received.append(text)
            cancel.set()

        imp = RetroImporter(api_key="x", on_utterance=on_utt, _post_fn=AsyncMock())
        utts = [
            {"speaker_id": "A", "text": "One."},
            {"speaker_id": "A", "text": "Two."},
            {"speaker_id": "A", "text": "Three."},
        ]
        count = await imp.process_utterances(utts, cancel_event=cancel)
        assert count == 1
        assert received == ["One."]

    @pytest.mark.asyncio
    async def test_defaults_missing_fields(self):
        received: list[dict] = []

        async def on_utt(sid, text, is_final, start_s, end_s):
            received.append({"speaker_id": sid, "start": start_s, "end": end_s})

        imp = RetroImporter(api_key="x", on_utterance=on_utt, _post_fn=AsyncMock())
        utts = [{"text": "No speaker or timing."}]
        await imp.process_utterances(utts)
        assert received[0]["speaker_id"] == "speaker_0"
        assert received[0]["start"] == 0.0
        assert received[0]["end"] == 0.0
