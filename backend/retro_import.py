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


# ---------------------------------------------------------------------------
# Transcript parsing — shared helpers
# ---------------------------------------------------------------------------

def _parse_timestamp(ts: str) -> float:
    """Convert ``HH:MM:SS.mmm``, ``HH:MM:SS,mmm``, or ``MM:SS`` to seconds."""
    ts = ts.strip()
    # HH:MM:SS with optional fractional part (. or , separator)
    m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?$", ts)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        frac = m.group(4)
        ms = int(frac.ljust(3, "0")) if frac else 0
        return h * 3600 + mi * 60 + s + ms / 1000
    # MM:SS
    m = re.match(r"(\d{1,2}):(\d{2})$", ts)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return 0.0


def _first_nonblank_line(text: str) -> str:
    """Return the first non-empty stripped line."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


# ---------------------------------------------------------------------------
# VTT / SRT / Teams / Google Meet / Zoom detection patterns
# ---------------------------------------------------------------------------

_VTT_TIMESTAMP_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})"
)
_VTT_VOICE_RE = re.compile(r"<v\s+([^>]+)>(.*)$")
_SRT_TIMESTAMP_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})"
)
_SRT_SPEAKER_RE = re.compile(r"^-?\s*([^:]{1,60}):\s+(.+)$")
_TEAMS_INLINE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"<v\s+([^>]+)>(.*?)(?:</v>)?\s*$"
)
_GMEET_SPEAKER_RE = re.compile(r"^(.+?)\s*\((\d{1,2}:\d{2}(?::\d{2})?)\)\s*$")
_ZOOM_BRACKET_RE = re.compile(
    r"^([^:\[\]]{1,60}):\s+\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+(.+)$"
)
_ZOOM_LEADING_TS_RE = re.compile(
    r"^(\d{1,2}:\d{2}:\d{2})\s+([^:]{1,60}):\s+(.+)$"
)


def _looks_like_srt(text: str) -> bool:
    """True if the text looks like an SRT subtitle file."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return (
        len(lines) >= 2
        and bool(re.match(r"^\d+$", lines[0]))
        and bool(_SRT_TIMESTAMP_RE.search(lines[1]))
    )


# ---------------------------------------------------------------------------
# Format-specific parsers
# ---------------------------------------------------------------------------

def _parse_vtt(text: str) -> list[dict]:
    """Parse a WebVTT file (Zoom, Teams, Google Meet)."""
    blocks = re.split(r"\n\s*\n", text)
    result: list[dict] = []
    current_speaker = "speaker_0"

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # Skip header, NOTE, STYLE blocks
        if block.startswith("WEBVTT") or block.startswith("NOTE") or block.startswith("STYLE"):
            continue

        lines = block.splitlines()
        ts_line = None
        text_lines: list[str] = []

        for line in lines:
            line = line.strip()
            if _VTT_TIMESTAMP_RE.search(line):
                ts_line = line
            elif re.match(r"^\d+$", line):
                # Cue identifier (numeric) — skip
                continue
            elif line:
                text_lines.append(line)

        if not text_lines:
            continue

        # Extract timestamps
        start, end = 0.0, 0.0
        if ts_line:
            m = _VTT_TIMESTAMP_RE.search(ts_line)
            if m:
                start = _parse_timestamp(m.group(1))
                end = _parse_timestamp(m.group(2))

        # Join text lines and extract speaker from voice tags
        full_text = " ".join(text_lines)
        # Remove closing </v> tags
        full_text = re.sub(r"</v>", "", full_text).strip()
        # Extract speaker from <v Name>
        vm = _VTT_VOICE_RE.match(full_text)
        if vm:
            current_speaker = vm.group(1).strip()
            full_text = vm.group(2).strip()
        # Handle inline voice tags in the middle
        full_text = re.sub(r"<v\s+[^>]+>", "", full_text).strip()

        if full_text:
            result.append({"speaker_id": current_speaker, "text": full_text, "start": start, "end": end})

    return result


def _parse_srt(text: str) -> list[dict]:
    """Parse an SRT subtitle file."""
    blocks = re.split(r"\n\s*\n", text)
    result: list[dict] = []
    current_speaker = "speaker_0"

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        ts_line = None
        text_lines: list[str] = []

        for line in lines:
            line = line.strip()
            if _SRT_TIMESTAMP_RE.search(line):
                ts_line = line
            elif re.match(r"^\d+$", line):
                continue  # cue number
            elif line:
                text_lines.append(line)

        if not text_lines:
            continue

        start, end = 0.0, 0.0
        if ts_line:
            m = _SRT_TIMESTAMP_RE.search(ts_line)
            if m:
                start = _parse_timestamp(m.group(1))
                end = _parse_timestamp(m.group(2))

        full_text = " ".join(text_lines)
        # Try to extract speaker from text
        sm = _SRT_SPEAKER_RE.match(full_text)
        if sm:
            current_speaker = sm.group(1).strip()
            full_text = sm.group(2).strip()

        if full_text:
            result.append({"speaker_id": current_speaker, "text": full_text, "start": start, "end": end})

    return result


def _parse_teams_inline_vtt(text: str) -> list[dict]:
    """Parse Teams inline VTT (timestamp + voice tag on same line)."""
    result: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TEAMS_INLINE_RE.match(line)
        if m:
            start = _parse_timestamp(m.group(1))
            end = _parse_timestamp(m.group(2))
            speaker = m.group(3).strip()
            utt_text = m.group(4).strip()
            # Remove any trailing </v> that wasn't caught by the regex
            utt_text = re.sub(r"</v>", "", utt_text).strip()
            if utt_text:
                result.append({"speaker_id": speaker, "text": utt_text, "start": start, "end": end})
    return result


def _parse_google_meet(text: str) -> list[dict]:
    """Parse Google Meet transcript (``Name (HH:MM:SS)`` on its own line)."""
    result: list[dict] = []
    current_speaker = "speaker_0"
    current_start = 0.0
    text_lines: list[str] = []

    def flush():
        if text_lines:
            full_text = " ".join(text_lines).strip()
            if full_text:
                result.append({
                    "speaker_id": current_speaker,
                    "text": full_text,
                    "start": current_start,
                    "end": 0.0,
                })
            text_lines.clear()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _GMEET_SPEAKER_RE.match(line)
        if m:
            flush()
            current_speaker = m.group(1).strip()
            current_start = _parse_timestamp(m.group(2))
        else:
            text_lines.append(line)

    flush()
    return result


def _parse_zoom_bracket(text: str) -> list[dict]:
    """Parse Zoom TXT with bracket timestamps: ``Name: [HH:MM:SS] text``."""
    result: list[dict] = []
    current_speaker = "speaker_0"
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _ZOOM_BRACKET_RE.match(line)
        if m:
            current_speaker = m.group(1).strip()
            start = _parse_timestamp(m.group(2))
            utt_text = m.group(3).strip()
            if utt_text:
                result.append({"speaker_id": current_speaker, "text": utt_text, "start": start, "end": 0.0})
        else:
            # Continuation line
            if line:
                result.append({"speaker_id": current_speaker, "text": line, "start": 0.0, "end": 0.0})
    return result


def _parse_zoom_leading_ts(text: str) -> list[dict]:
    """Parse Zoom TXT with leading timestamps: ``HH:MM:SS Name: text``."""
    result: list[dict] = []
    current_speaker = "speaker_0"
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _ZOOM_LEADING_TS_RE.match(line)
        if m:
            start = _parse_timestamp(m.group(1))
            current_speaker = m.group(2).strip()
            utt_text = m.group(3).strip()
            if utt_text:
                result.append({"speaker_id": current_speaker, "text": utt_text, "start": start, "end": 0.0})
        else:
            result.append({"speaker_id": current_speaker, "text": line, "start": 0.0, "end": 0.0})
    return result


# ---------------------------------------------------------------------------
# Main transcript parser — auto-detects format
# ---------------------------------------------------------------------------

def parse_text_transcript(text: str) -> list[dict]:
    """
    Parse a transcript into utterance dicts.

    Auto-detects format: JSON, WebVTT, SRT, Teams inline VTT,
    Google Meet, Zoom TXT (bracket/leading timestamp), Markdown bold,
    and plain ``Speaker: text``.

    All parsed speaker IDs are validated via ``_sanitize_speaker_ids`` —
    non-name strings (system terms, technical artifacts) are replaced with
    numbered ``speaker_N`` labels before being returned.

    Returns
    -------
    list of dicts with keys: speaker_id (str), text (str), start (float), end (float).
    Returns [] for blank input.
    """
    # Strip BOM (Windows-exported files)
    text = text.lstrip("\ufeff")
    text = text.strip()
    if not text:
        return []

    # ── JSON ───────────────────────────────────────────────────────────────
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
            return _sanitize_speaker_ids(result)

    # ── WebVTT ─────────────────────────────────────────────────────────────
    if text.startswith("WEBVTT"):
        return _sanitize_speaker_ids(_parse_vtt(text))

    # ── Teams inline VTT (no WEBVTT header) ────────────────────────────────
    first = _first_nonblank_line(text)
    if _TEAMS_INLINE_RE.match(first):
        return _sanitize_speaker_ids(_parse_teams_inline_vtt(text))

    # ── SRT ────────────────────────────────────────────────────────────────
    if _looks_like_srt(text):
        return _sanitize_speaker_ids(_parse_srt(text))

    # ── Google Meet (Name (HH:MM:SS) on its own line) ─────────────────────
    if _GMEET_SPEAKER_RE.match(first):
        return _sanitize_speaker_ids(_parse_google_meet(text))

    # ── Zoom bracket: Name: [HH:MM:SS] text ──────────────────────────────
    if _ZOOM_BRACKET_RE.match(first):
        return _sanitize_speaker_ids(_parse_zoom_bracket(text))

    # ── Zoom leading ts: HH:MM:SS Name: text ─────────────────────────────
    if _ZOOM_LEADING_TS_RE.match(first):
        return _sanitize_speaker_ids(_parse_zoom_leading_ts(text))

    # ── Plain text / Markdown bold fallback ────────────────────────────────
    _SPEAKER_LINE = re.compile(
        r"^"
        r"(?:\d{1,2}:\d{2}(?::\d{2})?\s+)?"   # optional leading timestamp
        r"\*{0,2}"                              # optional opening ** or *
        r"([^:*\n]{1,60})"                      # speaker name (no colons, no asterisks)
        r":\*{0,2}"                             # colon + optional closing ** or *
        r"\s+"                                  # whitespace
        r"(.+)$",                               # utterance text
    )
    _SKIP_LINE = re.compile(
        r"^(?:#|\*\*Date|\*\*Participants|\*\*Time|\*\*Location|---|===|\[|\*\*Meeting)",
        re.IGNORECASE,
    )

    lines = text.splitlines()
    result = []
    current_speaker = "speaker_0"
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _SKIP_LINE.match(line):
            continue
        m = _SPEAKER_LINE.match(line)
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
    return _sanitize_speaker_ids(result)


_GENERIC_SPEAKER_RE = re.compile(r"^(speaker|counterpart)_\d+$", re.IGNORECASE)


def _sanitize_speaker_ids(utterances: list[dict]) -> list[dict]:
    """
    Post-process parsed utterances: replace non-name speaker IDs with
    numbered speaker labels. This catches garbage that slipped through the
    regex-based parsers (e.g. "kill switch", "prompt timeout").

    Existing generic IDs (``speaker_0``, ``counterpart_1``) are kept as-is.
    Real names are kept as-is. Everything else is replaced with ``speaker_N``.

    Uses identity.is_plausible_speaker_name as the single source of truth
    for what constitutes a valid name.
    """
    from backend.identity import is_plausible_speaker_name

    # Build a stable mapping: original speaker_id → cleaned speaker_id
    seen: dict[str, str] = {}
    next_generic = 0

    # Find the highest existing generic speaker index so we don't collide
    for utt in utterances:
        sid = utt.get("speaker_id", "")
        m = _GENERIC_SPEAKER_RE.match(sid)
        if m:
            try:
                idx = int(sid.rsplit("_", 1)[1])
                next_generic = max(next_generic, idx + 1)
            except (ValueError, IndexError):
                pass

    for utt in utterances:
        sid = utt.get("speaker_id", "")
        if sid not in seen:
            if _GENERIC_SPEAKER_RE.match(sid):
                seen[sid] = sid  # keep existing generic IDs
            elif is_plausible_speaker_name(sid):
                seen[sid] = sid  # keep real names
            else:
                seen[sid] = f"speaker_{next_generic}"
                next_generic += 1
        utt["speaker_id"] = seen[sid]

    return utterances


_TEXT_EXTENSIONS = {".txt", ".json", ".jsonl", ".md", ".vtt", ".srt"}


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
