"""
Retroactive audio file processing via Deepgram REST API.

Architecture
────────────
  Audio file (WAV / MP3 / M4A / …)
       │
       │  read bytes
       ▼
  RetroImporter
       │
       │  POST https://api.deepgram.com/v1/listen
       │  (binary body, query params for diarization)
       ▼
  Deepgram JSON response
       │
       │  parse utterances
       ▼
  on_utterance(speaker_id, text, is_final=True, start_s, end_s)
  on_progress(delivered, total)   ← optional, fires after each utterance

Deepgram REST vs. streaming
────────────────────────────
The REST endpoint accepts an audio file as the request body and returns a
single JSON document once transcription is complete. ``utterances=true``
makes Deepgram segment the transcript by speaker automatically, which is
more ergonomic than reconstructing speaker turns from individual words.

The response schema (simplified):
  {
    "results": {
      "utterances": [
        {
          "start": float,        # seconds from file start
          "end": float,
          "transcript": str,
          "speaker": int,        # 0-based speaker index
          "words": [...]
        },
        ...
      ]
    }
  }

If ``utterances`` is absent (e.g. diarize=false or single-channel mono
without speech), the method falls back to the channel-level alternatives
and synthesises a single utterance per channel result.

Progress and cancellation
─────────────────────────
Supply ``on_progress`` to receive ``(delivered: int, total: int)`` after
each utterance is delivered to ``on_utterance``.  ``total`` is the number
of non-empty utterances found in the Deepgram response; ``delivered``
increments from 1 to total.

Pass a ``cancel_event`` (``asyncio.Event``) to ``process_file`` to abort
mid-delivery.  The event is checked *before* each utterance is fired so
cancellation is cooperative: already-delivered utterances are kept and the
method returns the count of utterances delivered before the stop.

Usage
─────
    async def handle(speaker_id, text, is_final, start_s, end_s):
        print(f"[{speaker_id}] {text}  ({start_s:.1f}s – {end_s:.1f}s)")

    async def progress(delivered, total):
        print(f"{delivered}/{total}")

    cancel = asyncio.Event()

    importer = RetroImporter(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        on_utterance=handle,
        on_progress=progress,
    )
    count = await importer.process_file(
        "/recordings/meeting_2024-01-15.wav",
        cancel_event=cancel,
    )
    print(f"Delivered {count} utterances")
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types  (same as transcription.py so callers can share callbacks)
# ---------------------------------------------------------------------------

UtteranceCallback = Callable[
    [str, str, bool, float, float],   # speaker_id, text, is_final, start_s, end_s
    Awaitable[None],
]

# Progress callback: called after each delivered utterance.
#   delivered — number of utterances fired so far (1-based)
#   total     — total non-empty utterances in the response
ProgressCallback = Callable[[int, int], Awaitable[None]]

# Injected async post function signature:
#   async def post(url, headers, params, data) -> dict
PostFn = Callable[..., Awaitable[dict]]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"

_DEFAULT_PARAMS: dict[str, str] = {
    "model": "nova-2",
    "diarize": "true",
    "punctuate": "true",
    "utterances": "true",
    "utt_split": "0.8",
}


# ---------------------------------------------------------------------------
# RetroImporter
# ---------------------------------------------------------------------------

class RetroImporter:
    """
    Submit a recorded audio file to Deepgram and fire ``on_utterance`` for
    each speaker turn found.

    Parameters
    ----------
    api_key:
        Deepgram API key.  Defaults to the ``DEEPGRAM_API_KEY`` env var.
    on_utterance:
        Async callback: ``async def cb(speaker_id, text, is_final, start_s, end_s)``
        ``is_final`` is always ``True`` for retro imports (the file is complete).
    on_progress:
        Optional async callback fired after each utterance is delivered:
        ``async def cb(delivered: int, total: int)``.
        ``total`` is the count of non-empty utterances in the Deepgram response;
        ``delivered`` increments from 1 to total.
    sample_rate:
        PCM sample rate hint sent to Deepgram (default 16 000 Hz).  Deepgram
        auto-detects for container formats (wav / mp3 / …); this is used when
        the file is raw PCM.
    _post_fn:
        Injectable async HTTP POST callable for testing.
        Signature: ``async def post(url, *, headers, params, content) -> dict``
        Defaults to an ``httpx``-based implementation.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        on_utterance: UtteranceCallback,
        on_progress: ProgressCallback | None = None,
        sample_rate: int = 16_000,
        _post_fn: PostFn | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("DEEPGRAM_API_KEY", "")
        self._on_utterance = on_utterance
        self._on_progress = on_progress
        self._sample_rate = sample_rate
        self._post_fn = _post_fn or _httpx_post

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_utterances(
        self,
        utterances: list[dict],
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> int:
        """
        Deliver a pre-parsed list of utterance dicts via ``on_utterance``.

        Use this instead of ``process_file`` when the transcript has already
        been parsed (e.g. from ``parse_text_transcript``).

        Each dict must have: ``speaker_id`` (str), ``text`` (str).
        Optional: ``start`` (float, default 0.0), ``end`` (float, default 0.0).

        Returns the number of utterances delivered.
        """
        non_empty = [u for u in utterances if str(u.get("text", "")).strip()]
        total = len(non_empty)
        count = 0
        for utt in non_empty:
            if cancel_event and cancel_event.is_set():
                break
            speaker_id = str(utt.get("speaker_id", "speaker_0"))
            text = str(utt["text"]).strip()
            start_s = float(utt.get("start", 0.0))
            end_s = float(utt.get("end", 0.0))
            await self._on_utterance(speaker_id, text, True, start_s, end_s)
            count += 1
            if self._on_progress:
                await self._on_progress(count, total)
        logger.info("RetroImporter: delivered %d utterances from text transcript", count)
        return count

    async def process_file(
        self,
        file_path: str | Path,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> int:
        """
        Submit *file_path* to Deepgram and deliver utterances via the
        ``on_utterance`` callback.

        Parameters
        ----------
        file_path:
            Path to the audio file to process.
        cancel_event:
            Optional :class:`asyncio.Event`.  When set, delivery stops after
            the current utterance completes.  The method returns the count of
            utterances delivered before cancellation.

        Returns the number of utterances delivered.

        Raises
        ------
        FileNotFoundError
            If *file_path* does not exist.
        RuntimeError
            If Deepgram returns an error response.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        audio_bytes = path.read_bytes()
        content_type = _content_type_for(path)

        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": content_type,
        }
        params = dict(_DEFAULT_PARAMS)
        params["sample_rate"] = str(self._sample_rate)

        logger.info(
            "RetroImporter: submitting %s (%d bytes) to Deepgram",
            path.name, len(audio_bytes),
        )

        response = await self._post_fn(
            _DEEPGRAM_URL,
            headers=headers,
            params=params,
            content=audio_bytes,
        )

        # Check for Deepgram error payload
        if "error" in response:
            raise RuntimeError(
                f"Deepgram error: {response.get('error')} — {response.get('message', '')}"
            )

        count = await self._deliver_utterances(response, cancel_event)
        logger.info("RetroImporter: delivered %d utterances from %s", count, path.name)
        return count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _deliver_utterances(
        self,
        response: dict,
        cancel_event: asyncio.Event | None,
    ) -> int:
        """Parse the Deepgram response and fire on_utterance for each turn."""
        results: dict = response.get("results", {})

        utterances: list[dict] = results.get("utterances", [])
        if utterances:
            return await self._deliver_from_utterances(utterances, cancel_event)

        # Fallback: no utterances block — synthesise from channel alternatives
        return await self._deliver_from_channels(results, cancel_event)

    async def _deliver_from_utterances(
        self,
        utterances: list[dict],
        cancel_event: asyncio.Event | None,
    ) -> int:
        # Pre-count non-empty utterances so progress totals are accurate.
        total = sum(
            1 for u in utterances if u.get("transcript", "").strip()
        )
        count = 0
        for utt in utterances:
            if cancel_event and cancel_event.is_set():
                logger.info(
                    "RetroImporter: delivery cancelled after %d/%d utterances",
                    count, total,
                )
                break
            transcript: str = utt.get("transcript", "").strip()
            if not transcript:
                continue
            speaker_id = f"speaker_{utt.get('speaker', 0)}"
            start_s = float(utt.get("start", 0.0))
            end_s = float(utt.get("end", 0.0))
            await self._on_utterance(speaker_id, transcript, True, start_s, end_s)
            count += 1
            if self._on_progress:
                await self._on_progress(count, total)
        return count

    async def _deliver_from_channels(
        self,
        results: dict,
        cancel_event: asyncio.Event | None,
    ) -> int:
        """Fallback path: one utterance per channel alternative."""
        channels: list[dict] = results.get("channels", [])
        # Pre-count non-empty channel transcripts for accurate totals.
        total = sum(
            1
            for ch in channels
            if ch.get("alternatives") and ch["alternatives"][0].get("transcript", "").strip()
        )
        count = 0
        for channel in channels:
            if cancel_event and cancel_event.is_set():
                logger.info(
                    "RetroImporter: delivery cancelled after %d/%d channels",
                    count, total,
                )
                break
            alternatives: list[dict] = channel.get("alternatives", [])
            if not alternatives:
                continue
            best = alternatives[0]
            transcript: str = best.get("transcript", "").strip()
            if not transcript:
                continue
            # Duration info lives at the top-level result if present
            start_s = 0.0
            end_s = 0.0
            words: list[dict] = best.get("words", [])
            if words:
                start_s = float(words[0].get("start", 0.0))
                end_s = float(words[-1].get("end", 0.0))
            speaker_id = _speaker_from_words(words)
            await self._on_utterance(speaker_id, transcript, True, start_s, end_s)
            count += 1
            if self._on_progress:
                await self._on_progress(count, total)
        return count


# ---------------------------------------------------------------------------
# Default HTTP implementation (httpx)
# ---------------------------------------------------------------------------

async def _httpx_post(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str],
    content: bytes,
) -> dict[str, Any]:
    """Send a POST request and return the parsed JSON body."""
    import httpx  # lazy import so the module is importable without httpx installed

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        resp = await client.post(url, headers=headers, params=params, content=content)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_type_for(path: Path) -> str:
    """Return a MIME type suitable for the audio file extension."""
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("audio/"):
        # Normalise platform variants (e.g. audio/x-wav → audio/wav)
        return mime.replace("audio/x-wav", "audio/wav")
    # Raw PCM or unknown — default Deepgram accepts
    return "audio/wav"


def parse_text_transcript(text: str) -> list[dict]:
    """
    Parse a plain-text or JSON transcript into utterance dicts.

    Supported input formats
    -----------------------
    **JSON array** (most structured):

        [
          {"speaker": "Alice", "text": "Hello everyone.", "start": 0.0, "end": 2.1},
          {"speaker_id": "speaker_1", "text": "Thanks for joining.", "start": 2.5, "end": 5.0}
        ]

    Integer ``speaker`` values are mapped to ``"speaker_N"``; string values
    are used verbatim as the speaker_id.  ``start``/``end`` default to 0.0 if
    absent.

    **Deepgram JSON** (``{"results": {"utterances": [...]}}``) is also accepted.

    **Plain text** (one utterance per non-blank line):

        Alice: Hello everyone.
        Bob: Thanks for joining.
        Unknown line with no colon also works — attributed to previous speaker.

    Leading/trailing whitespace is stripped from both speaker and text.
    Lines with no colon are attributed to the last seen speaker (or
    ``"speaker_0"`` for the very first line).

    Returns
    -------
    list of dicts with keys: speaker_id (str), text (str), start (float), end (float).
    Returns [] for blank input.
    """
    text = text.strip()
    if not text:
        return []

    # ── Try JSON first ──────────────────────────────────────────────────────
    if text.startswith(("[", "{")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass  # fall through to plain text
        else:
            # Deepgram wrapper
            if isinstance(data, dict):
                data = (
                    data.get("results", {}).get("utterances", [])
                    or data.get("utterances", [])
                )

            result = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                raw_text = str(item.get("text") or item.get("transcript") or "").strip()
                if not raw_text:
                    continue
                spk = item.get("speaker_id") or item.get("speaker")
                if spk is None:
                    speaker_id = "speaker_0"
                elif isinstance(spk, int):
                    speaker_id = f"speaker_{spk}"
                else:
                    speaker_id = str(spk).strip()
                start = float(item.get("start", 0.0))
                end = float(item.get("end", 0.0))
                result.append({"speaker_id": speaker_id, "text": raw_text, "start": start, "end": end})
            return result

    # ── Plain text: "Speaker Name: utterance text" ──────────────────────────
    lines = text.splitlines()
    result = []
    current_speaker = "speaker_0"
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([^:]{1,60}):\s+(.+)$", line)
        if m:
            current_speaker = m.group(1).strip()
            utterance_text = m.group(2).strip()
        else:
            utterance_text = line
        result.append({
            "speaker_id": current_speaker,
            "text": utterance_text,
            "start": 0.0,
            "end": 0.0,
        })
    return result


_TEXT_EXTENSIONS = {".txt", ".json", ".jsonl", ".md"}


def is_text_transcript(filename: str) -> bool:
    """Return True if the filename looks like a text/JSON transcript rather than audio."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in _TEXT_EXTENSIONS


def _speaker_from_words(words: list[dict]) -> str:
    """Majority-vote speaker ID from word-level diarization data."""
    if not words:
        return "speaker_0"
    counts: dict[int, int] = {}
    for w in words:
        s = w.get("speaker")
        if s is not None:
            counts[s] = counts.get(s, 0) + 1
    if not counts:
        return "speaker_0"
    dominant = max(counts, key=lambda k: (counts[k], -k))
    return f"speaker_{dominant}"
