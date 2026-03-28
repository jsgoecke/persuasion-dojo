"""
Parse text transcripts into speaker-labelled utterances.

Supports three common formats:

1. **Markdown bold** — ``**Speaker:** text`` or ``**Speaker**: text``
2. **Otter.ai / Zoom** — ``Speaker Name  0:42\\ntext`` (name on its own line with timestamp)
3. **Simple colon** — ``Speaker: text``

The parser auto-detects the format by scanning the first few lines.

Usage
-----
    from backend.transcript_parser import parse_transcript

    utterances = parse_transcript(raw_text)
    # [{"speaker": "Sam", "text": "I think we should...", "start": 0.0, "end": 0.0}, ...]
"""

from __future__ import annotations

import re
from typing import Literal

# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

TranscriptFormat = Literal["markdown", "otter", "simple", "unknown"]


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

_MARKDOWN_RE = re.compile(r"^\*\*(.+?)(?::\s*\*\*|\*\*\s*:)\s*(.+)", re.MULTILINE)
_OTTER_RE = re.compile(
    r"^([A-Z][A-Za-z\s.\-']+?)\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$",
    re.MULTILINE,
)
_SIMPLE_RE = re.compile(r"^([A-Z][A-Za-z\s.\-']+?):\s+(.+)", re.MULTILINE)

# Timestamp pattern: H:MM:SS or M:SS or MM:SS
_TIMESTAMP_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def _detect_format(text: str) -> TranscriptFormat:
    """Detect transcript format from sample of first 2000 characters."""
    sample = text[:2000]
    md_count = len(_MARKDOWN_RE.findall(sample))
    otter_count = len(_OTTER_RE.findall(sample))
    simple_count = len(_SIMPLE_RE.findall(sample))

    if md_count >= 2:
        return "markdown"
    if otter_count >= 2:
        return "otter"
    if simple_count >= 2:
        return "simple"

    # Fall back: whichever has the most hits
    best = max(
        [("markdown", md_count), ("otter", otter_count), ("simple", simple_count)],
        key=lambda x: x[1],
    )
    return best[0] if best[1] > 0 else "unknown"


def _parse_timestamp(ts: str) -> float:
    """Convert H:MM:SS or M:SS to seconds."""
    m = _TIMESTAMP_RE.match(ts)
    if not m:
        return 0.0
    parts = m.groups()
    if parts[2] is not None:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0]) * 60 + int(parts[1])


# ---------------------------------------------------------------------------
# Format-specific parsers
# ---------------------------------------------------------------------------

def _parse_markdown(text: str) -> list[dict]:
    """Parse **Speaker:** text format."""
    results = []
    for match in _MARKDOWN_RE.finditer(text):
        speaker = match.group(1).strip()
        utterance = match.group(2).strip()
        if speaker and utterance:
            results.append({
                "speaker": speaker, "text": utterance,
                "start": 0.0, "end": 0.0,
            })
    return results


def _parse_otter(text: str) -> list[dict]:
    """
    Parse Otter.ai / Zoom format:
        Speaker Name  0:42
        The utterance text goes here...

    Speaker line has name + timestamp. Everything until the next speaker line
    is the utterance text.
    """
    results = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = _OTTER_RE.match(lines[i])
        if m:
            speaker = m.group(1).strip()
            timestamp = _parse_timestamp(m.group(2))
            # Collect text lines until next speaker header or end
            text_lines = []
            i += 1
            while i < len(lines):
                if _OTTER_RE.match(lines[i]):
                    break
                line = lines[i].strip()
                if line:
                    text_lines.append(line)
                i += 1
            utterance = " ".join(text_lines).strip()
            if speaker and utterance:
                results.append({
                    "speaker": speaker, "text": utterance,
                    "start": timestamp, "end": timestamp,
                })
        else:
            i += 1
    return results


def _parse_simple(text: str) -> list[dict]:
    """Parse Speaker: text format (one per line or multi-line)."""
    results = []
    lines = text.split("\n")
    current_speaker = None
    current_text: list[str] = []

    for line in lines:
        m = _SIMPLE_RE.match(line)
        if m:
            # Save previous
            if current_speaker and current_text:
                results.append({
                    "speaker": current_speaker,
                    "text": " ".join(current_text).strip(),
                    "start": 0.0, "end": 0.0,
                })
            current_speaker = m.group(1).strip()
            current_text = [m.group(2).strip()]
        elif current_speaker and line.strip():
            # Continuation of previous speaker's text
            current_text.append(line.strip())

    # Don't forget the last one
    if current_speaker and current_text:
        results.append({
            "speaker": current_speaker,
            "text": " ".join(current_text).strip(),
            "start": 0.0, "end": 0.0,
        })
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_transcript(text: str) -> list[dict]:
    """
    Parse a text transcript into speaker-labelled utterances.

    Auto-detects format (markdown bold, Otter.ai/Zoom, or simple colon).

    Parameters
    ----------
    text : str
        Raw transcript text.

    Returns
    -------
    list[dict]
        Each dict has: speaker (str), text (str), start (float), end (float).
        Speaker names are preserved as-is (not converted to speaker_N).
        Returns empty list if no utterances could be parsed.
    """
    if not text or not text.strip():
        return []

    fmt = _detect_format(text)

    if fmt == "markdown":
        return _parse_markdown(text)
    elif fmt == "otter":
        return _parse_otter(text)
    elif fmt == "simple":
        return _parse_simple(text)
    else:
        # Try all parsers and return whichever finds the most
        results = [_parse_markdown(text), _parse_otter(text), _parse_simple(text)]
        return max(results, key=len)


def detect_format(text: str) -> TranscriptFormat:
    """Expose format detection for diagnostics."""
    return _detect_format(text)
