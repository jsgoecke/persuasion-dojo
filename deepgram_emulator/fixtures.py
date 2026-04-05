"""
Fixture loader for the Deepgram emulator.

Loads JSON fixture files from a directory and indexes them by scenario name.
Each fixture contains both streaming events (for WebSocket) and a REST response.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Fixture:
    scenario: str
    streaming_events: list[dict] = field(default_factory=list)
    rest_response: dict = field(default_factory=dict)


_DEFAULT_FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "tests", "fixtures", "deepgram"
)


def load_fixtures(fixtures_dir: str | None = None) -> dict[str, Fixture]:
    """Load all JSON fixtures from the given directory."""
    d = fixtures_dir or _DEFAULT_FIXTURES_DIR
    result: dict[str, Fixture] = {}
    p = Path(d)
    if not p.is_dir():
        return result
    for f in sorted(p.glob("*.json")):
        with open(f) as fh:
            data = json.load(fh)
        scenario = data.get("scenario", f.stem)
        result[scenario] = Fixture(
            scenario=scenario,
            streaming_events=data.get("streaming_events", []),
            rest_response=data.get("rest_response", {}),
        )
    return result


def get_default_fixture(fixtures: dict[str, Fixture]) -> Fixture | None:
    """Return the first fixture (alphabetical) or None if empty."""
    if not fixtures:
        return None
    return next(iter(fixtures.values()))
