"""
Self-evolving coaching playbook — a markdown file that Opus rewrites after
each session based on what worked and what didn't.

Architecture
────────────
  Session ends → effectiveness scores computed per prompt
       │
       ▼
  update_playbook()
       │  reads current playbook.md
       │  builds a summary of this session's outcomes
       │  asks Opus to rewrite the playbook with new evidence
       ▼
  data/playbooks/{user_id}.md  (overwritten with updated version)

  Next session starts → CoachingEngine reads playbook
       │
       ▼
  get_coaching_context()
       │  extracts relevant sections for the current counterpart/state
       ▼
  Included in Haiku prompt as additional context

The playbook is structured markdown with these sections:
  - Effective Patterns: what consistently improves convergence
  - Ineffective Patterns: what consistently fails or hurts
  - Pairing Notes: per-archetype insights from real sessions
  - Session Trends: aggregate patterns (talk time, ego safety, timing)

Opus has full authority to rewrite, merge, prune, or restructure the
playbook. Old learnings that are contradicted by new data get removed.
The file is capped at ~2000 words to stay within Haiku's useful context.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "playbooks"

INITIAL_PLAYBOOK = """\
# Coaching Playbook

_This playbook evolves automatically after each session based on what works._

## Effective Patterns
_No patterns recorded yet. Complete a session to start learning._

## Ineffective Patterns
_No patterns recorded yet._

## Pairing Notes
_Archetype-specific insights will appear here after sessions._

## Session Trends
_Aggregate patterns will emerge over multiple sessions._
"""


def _playbook_path(user_id: str) -> Path:
    return _DATA_DIR / f"{user_id}.md"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_playbook(user_id: str) -> str:
    """Return the current playbook contents, or the initial template."""
    path = _playbook_path(user_id)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return INITIAL_PLAYBOOK


def get_coaching_context(
    user_id: str,
    counterpart_archetype: str | None = None,
    elm_state: str | None = None,
) -> str:
    """
    Extract relevant coaching context from the playbook for a Haiku prompt.

    Returns a compact string (≤500 words) with the most relevant learnings
    for the current coaching situation.
    """
    playbook = read_playbook(user_id)

    # If the playbook is just the initial template, return nothing
    if "No patterns recorded yet" in playbook:
        return ""

    # Build a focused extract
    sections: list[str] = []

    # Always include effective/ineffective patterns
    for heading in ("## Effective Patterns", "## Ineffective Patterns"):
        section = _extract_section(playbook, heading)
        if section and "No patterns recorded" not in section:
            sections.append(section)

    # Include pairing-specific notes if we have a counterpart
    if counterpart_archetype:
        pairing_section = _extract_subsection(
            playbook, "## Pairing Notes", counterpart_archetype
        )
        if pairing_section:
            sections.append(pairing_section)

    # Include ELM-specific notes if triggered
    if elm_state:
        elm_label = elm_state.replace("_", " ")
        for section_text in sections[:]:
            # Already captured — just make sure ELM-relevant lines are present
            pass
        # Also look for ELM mentions in effective/ineffective patterns
        elm_section = _extract_lines_mentioning(playbook, elm_label)
        if elm_section:
            sections.append(f"Relevant to {elm_label}:\n{elm_section}")

    if not sections:
        return ""

    context = "\n\n".join(sections)
    # Cap at ~500 words to avoid bloating Haiku's prompt
    words = context.split()
    if len(words) > 500:
        context = " ".join(words[:500]) + "…"

    return f"YOUR COACHING PLAYBOOK (learned from prior sessions):\n{context}"


# ---------------------------------------------------------------------------
# Update — called after each session
# ---------------------------------------------------------------------------

async def update_playbook(
    user_id: str,
    user_archetype: str,
    session_summary: dict,
    *,
    api_key: str | None = None,
) -> None:
    """
    Ask Opus to rewrite the playbook incorporating new session evidence.

    session_summary should contain:
        - persuasion_score: int (0-100)
        - timing_score, ego_safety_score, convergence_score: component scores
        - ego_threat_events: int
        - prompt_results: list of dicts with:
            - triggered_by: str (e.g. "elm:ego_threat", "cadence:self")
            - counterpart_archetype: str
            - text: str (the coaching tip shown)
            - effectiveness_score: float | None
            - convergence_before: float | None
            - convergence_after: float | None
        - talk_time_ratio: float
        - total_utterances: int
        - context: str (e.g. "board", "sales")
    """
    from anthropic import AsyncAnthropic

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.info("No API key — skipping playbook update")
        return

    current = read_playbook(user_id)
    evidence = _format_session_evidence(user_archetype, session_summary)

    prompt = (
        "You are maintaining a coaching playbook for an executive communication coach. "
        "The playbook is a markdown file that evolves over time based on real session data.\n\n"
        f"CURRENT PLAYBOOK:\n```markdown\n{current}\n```\n\n"
        f"NEW SESSION EVIDENCE:\n{evidence}\n\n"
        "INSTRUCTIONS:\n"
        "1. Rewrite the playbook incorporating the new evidence.\n"
        "2. If new data contradicts old patterns, update or remove the old ones.\n"
        "3. Keep patterns that are reinforced by new data — note how many sessions support them.\n"
        "4. Be specific: cite archetype pairings, ELM states, and concrete tactics.\n"
        "5. Under 'Pairing Notes', use ### subheadings per counterpart archetype.\n"
        "6. Under 'Session Trends', track aggregate stats (avg score, talk time sweet spot, etc.).\n"
        "7. Keep the playbook under 2000 words. Prune weak or one-off observations.\n"
        "8. Write in second person ('you' = the coached user).\n"
        "9. Be direct and actionable — this is read by an AI that generates real-time coaching tips.\n\n"
        "Output ONLY the updated markdown. No explanation, no code fences."
    )

    try:
        client = AsyncAnthropic(api_key=key)
        response = await asyncio.wait_for(
            client.messages.create(
                model="claude-opus-4-6",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=45.0,
        )
        updated = response.content[0].text.strip()

        # Sanity check — must start with a heading and be reasonable length
        if updated.startswith("#") and len(updated) > 100:
            _playbook_path(user_id).parent.mkdir(parents=True, exist_ok=True)
            _playbook_path(user_id).write_text(updated, encoding="utf-8")
            logger.info(
                "Playbook updated for user %s (%d chars)",
                user_id, len(updated),
            )
        else:
            logger.warning("Opus returned unexpected playbook format, skipping write")
    except Exception as exc:
        logger.warning("Playbook update failed for user %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_session_evidence(user_archetype: str, summary: dict) -> str:
    """Format session data into a readable evidence block for Opus."""
    lines = [
        f"User archetype: {user_archetype}",
        f"Meeting context: {summary.get('context', 'unknown')}",
        f"Persuasion Score: {summary.get('persuasion_score', '?')}/100",
        f"  Timing: {summary.get('timing_score', '?')}",
        f"  Ego Safety: {summary.get('ego_safety_score', '?')}",
        f"  Convergence: {summary.get('convergence_score', '?')}",
        f"Ego threat events: {summary.get('ego_threat_events', 0)}",
        f"Talk time ratio: {summary.get('talk_time_ratio', '?')}",
        f"Total utterances: {summary.get('total_utterances', '?')}",
        "",
        "Coaching prompts and their effectiveness:",
    ]

    prompt_results = summary.get("prompt_results", [])
    if not prompt_results:
        lines.append("  (no prompts with effectiveness data)")
    else:
        for pr in prompt_results:
            eff = pr.get("effectiveness_score")
            eff_label = f"{eff:.2f}" if eff is not None else "n/a"
            before = pr.get("convergence_before")
            after = pr.get("convergence_after")
            delta = ""
            if before is not None and after is not None:
                delta = f" (convergence {before:.2f} → {after:.2f})"
            lines.append(
                f"  - [{pr.get('triggered_by', '?')}] "
                f"→ {pr.get('counterpart_archetype', '?')}: "
                f"\"{pr.get('text', '')[:80]}\" "
                f"effectiveness={eff_label}{delta}"
            )

    return "\n".join(lines)


def _extract_section(text: str, heading: str) -> str:
    """Extract a markdown section by heading (## level)."""
    lines = text.split("\n")
    collecting = False
    result: list[str] = []
    for line in lines:
        if line.strip() == heading:
            collecting = True
            result.append(line)
            continue
        if collecting:
            if line.startswith("## ") and line.strip() != heading:
                break
            result.append(line)
    return "\n".join(result).strip()


def _extract_subsection(text: str, parent_heading: str, keyword: str) -> str:
    """Extract a ### subsection within a ## section that matches a keyword."""
    section = _extract_section(text, parent_heading)
    if not section:
        return ""
    lines = section.split("\n")
    collecting = False
    result: list[str] = []
    for line in lines:
        if line.startswith("### ") and keyword.lower() in line.lower():
            collecting = True
            result.append(line)
            continue
        if collecting:
            if line.startswith("### ") or line.startswith("## "):
                break
            result.append(line)
    return "\n".join(result).strip()


def _extract_lines_mentioning(text: str, keyword: str) -> str:
    """Pull lines that mention a keyword (case-insensitive)."""
    kw = keyword.lower()
    matches = [
        line.strip()
        for line in text.split("\n")
        if kw in line.lower() and not line.startswith("#")
    ]
    return "\n".join(matches[:5])  # cap at 5 lines
