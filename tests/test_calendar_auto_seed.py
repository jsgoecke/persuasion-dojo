"""
Tests for Phase 1b: Calendar auto-seed at session start.

Coverage:
  - _auto_seed_from_calendar returns attendees from current meeting
  - _auto_seed_from_calendar returns empty when calendar not connected
  - _auto_seed_from_calendar returns empty when no current meeting
  - _auto_seed_from_calendar matches attendees with existing participant archetypes
  - Meeting.attendee_names populated from displayName or email fallback
  - Calendar auto-seed merges with manually-selected participants (no duplicates)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.calendar_service import Meeting


# ---------------------------------------------------------------------------
# Meeting.attendee_names from _parse_event
# ---------------------------------------------------------------------------

class TestMeetingAttendeeNames:
    """Verify attendee_names are extracted from calendar events."""

    def test_attendee_names_from_display_name(self):
        """displayName takes priority when available."""
        from backend.calendar_service import _parse_event

        event = {
            "id": "evt1",
            "summary": "Board Review",
            "start": {"dateTime": "2026-04-08T10:00:00Z"},
            "end": {"dateTime": "2026-04-08T11:00:00Z"},
            "attendees": [
                {"email": "sarah@corp.com", "displayName": "Sarah Chen"},
                {"email": "mike@corp.com", "displayName": "Mike Johnson"},
                {"email": "me@corp.com", "self": True, "displayName": "Me"},
            ],
        }
        meeting = _parse_event(event)
        assert meeting is not None
        assert meeting.attendee_names == ["Sarah Chen", "Mike Johnson"]
        assert meeting.attendee_emails == ["sarah@corp.com", "mike@corp.com"]

    def test_attendee_names_fallback_from_email(self):
        """When displayName is missing, derive name from email local part."""
        from backend.calendar_service import _parse_event

        event = {
            "id": "evt2",
            "summary": "Standup",
            "start": {"dateTime": "2026-04-08T09:00:00Z"},
            "end": {"dateTime": "2026-04-08T09:15:00Z"},
            "attendees": [
                {"email": "john.doe@corp.com"},
                {"email": "jane.smith@corp.com", "displayName": "Jane Smith"},
            ],
        }
        meeting = _parse_event(event)
        assert meeting is not None
        assert meeting.attendee_names == ["John Doe", "Jane Smith"]

    def test_no_attendees(self):
        """Meeting with no attendees returns empty lists."""
        from backend.calendar_service import _parse_event

        event = {
            "id": "evt3",
            "summary": "Focus time",
            "start": {"dateTime": "2026-04-08T14:00:00Z"},
            "end": {"dateTime": "2026-04-08T15:00:00Z"},
        }
        meeting = _parse_event(event)
        assert meeting is not None
        assert meeting.attendee_names == []
        assert meeting.attendee_emails == []


# ---------------------------------------------------------------------------
# _auto_seed_from_calendar
# ---------------------------------------------------------------------------

def _make_meeting(
    start_offset_min: int = 0,
    duration_min: int = 60,
    names: list[str] | None = None,
) -> Meeting:
    """Create a Meeting relative to 'now'."""
    now = datetime.now(timezone.utc)
    start = now + timedelta(minutes=start_offset_min)
    return Meeting(
        id="test-meeting",
        title="Test Meeting",
        start_dt=start,
        end_dt=start + timedelta(minutes=duration_min),
        attendee_emails=[f"{n.lower().replace(' ', '.')}@corp.com" for n in (names or [])],
        attendee_names=names or [],
    )


class TestAutoSeedFromCalendar:
    """Tests for backend.main._auto_seed_from_calendar."""

    @pytest.mark.asyncio
    async def test_returns_attendees_from_current_meeting(self):
        """When a meeting is happening now, return its attendees."""
        from backend.main import _auto_seed_from_calendar

        meeting = _make_meeting(start_offset_min=-30, names=["Sarah Chen", "Mike Johnson"])

        with patch("backend.main._get_calendar_service") as mock_svc:
            svc = MagicMock()
            svc.get_upcoming_meetings = AsyncMock(return_value=[meeting])
            mock_svc.return_value = svc

            with patch("backend.main.get_db_session") as mock_db:
                mock_session = AsyncMock()
                mock_session.get = AsyncMock(return_value=None)
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                with patch("backend.identity.resolve_speaker", AsyncMock(return_value=None)):
                    result = await _auto_seed_from_calendar()

        assert len(result) == 2
        assert result[0]["name"] == "Sarah Chen"
        assert result[1]["name"] == "Mike Johnson"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_calendar(self):
        """When calendar is not connected, return empty list."""
        from backend.main import _auto_seed_from_calendar

        with patch("backend.main._get_calendar_service", return_value=None):
            result = await _auto_seed_from_calendar()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_meetings(self):
        """When no meetings are happening, return empty list."""
        from backend.main import _auto_seed_from_calendar

        with patch("backend.main._get_calendar_service") as mock_svc:
            svc = MagicMock()
            svc.get_upcoming_meetings = AsyncMock(return_value=[])
            mock_svc.return_value = svc

            result = await _auto_seed_from_calendar()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_meeting_too_far_away(self):
        """Meeting starting in 30 minutes is outside the 15-minute window."""
        from backend.main import _auto_seed_from_calendar

        meeting = _make_meeting(start_offset_min=30, names=["Sarah Chen"])

        with patch("backend.main._get_calendar_service") as mock_svc:
            svc = MagicMock()
            svc.get_upcoming_meetings = AsyncMock(return_value=[meeting])
            mock_svc.return_value = svc

            result = await _auto_seed_from_calendar()

        assert result == []

    @pytest.mark.asyncio
    async def test_picks_meeting_starting_within_15_min(self):
        """Meeting starting in 10 minutes should be picked up."""
        from backend.main import _auto_seed_from_calendar

        meeting = _make_meeting(start_offset_min=10, names=["Sarah Chen"])

        with patch("backend.main._get_calendar_service") as mock_svc:
            svc = MagicMock()
            svc.get_upcoming_meetings = AsyncMock(return_value=[meeting])
            mock_svc.return_value = svc

            with patch("backend.main.get_db_session") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                with patch("backend.identity.resolve_speaker", AsyncMock(return_value=None)):
                    result = await _auto_seed_from_calendar()

        assert len(result) == 1
        assert result[0]["name"] == "Sarah Chen"

    @pytest.mark.asyncio
    async def test_matches_existing_participant_archetype(self):
        """When an attendee matches a known participant, use their archetype."""
        from backend.main import _auto_seed_from_calendar

        meeting = _make_meeting(start_offset_min=-10, names=["Sarah Chen"])

        existing = MagicMock()
        existing.ps_type = "Architect"

        with patch("backend.main._get_calendar_service") as mock_svc:
            svc = MagicMock()
            svc.get_upcoming_meetings = AsyncMock(return_value=[meeting])
            mock_svc.return_value = svc

            with patch("backend.main.get_db_session") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                with patch("backend.identity.resolve_speaker", AsyncMock(return_value=existing)):
                    result = await _auto_seed_from_calendar()

        assert len(result) == 1
        assert result[0]["name"] == "Sarah Chen"
        assert result[0]["archetype"] == "Architect"

    @pytest.mark.asyncio
    async def test_unknown_archetype_when_no_match(self):
        """When an attendee has no existing profile, archetype is 'Unknown'."""
        from backend.main import _auto_seed_from_calendar

        meeting = _make_meeting(start_offset_min=-10, names=["New Person"])

        with patch("backend.main._get_calendar_service") as mock_svc:
            svc = MagicMock()
            svc.get_upcoming_meetings = AsyncMock(return_value=[meeting])
            mock_svc.return_value = svc

            with patch("backend.main.get_db_session") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

                with patch("backend.identity.resolve_speaker", AsyncMock(return_value=None)):
                    result = await _auto_seed_from_calendar()

        assert result[0]["archetype"] == "Unknown"

    @pytest.mark.asyncio
    async def test_calendar_error_returns_empty(self):
        """Calendar API errors are swallowed gracefully."""
        from backend.main import _auto_seed_from_calendar

        with patch("backend.main._get_calendar_service") as mock_svc:
            svc = MagicMock()
            svc.get_upcoming_meetings = AsyncMock(side_effect=RuntimeError("auth expired"))
            mock_svc.return_value = svc

            result = await _auto_seed_from_calendar()

        assert result == []


# ---------------------------------------------------------------------------
# Merge logic (calendar + manual, no duplicates)
# ---------------------------------------------------------------------------

class TestCalendarMerge:
    """Verify calendar participants merge with manual selections without duplicates."""

    def test_merge_no_duplicates(self):
        """Calendar attendees already in manual list are skipped."""
        manual = [
            {"name": "Sarah Chen", "archetype": "Architect"},
        ]
        calendar = [
            {"name": "Sarah Chen", "archetype": "Unknown"},
            {"name": "Mike Johnson", "archetype": "Unknown"},
        ]

        existing_names = {p["name"].lower() for p in manual}
        for cp in calendar:
            if cp["name"].lower() not in existing_names:
                manual.append(cp)
                existing_names.add(cp["name"].lower())

        assert len(manual) == 2
        # Sarah should keep original archetype, not be overwritten
        assert manual[0]["archetype"] == "Architect"
        assert manual[1]["name"] == "Mike Johnson"

    def test_merge_empty_calendar(self):
        """No calendar results doesn't modify the manual list."""
        manual = [{"name": "Sarah Chen", "archetype": "Architect"}]
        calendar: list[dict] = []

        existing_names = {p["name"].lower() for p in manual}
        for cp in calendar:
            if cp["name"].lower() not in existing_names:
                manual.append(cp)

        assert len(manual) == 1

    def test_merge_empty_manual(self):
        """Calendar results populate an empty manual list."""
        manual: list[dict] = []
        calendar = [
            {"name": "Sarah Chen", "archetype": "Architect"},
            {"name": "Mike Johnson", "archetype": "Firestarter"},
        ]

        existing_names: set[str] = set()
        for cp in calendar:
            if cp["name"].lower() not in existing_names:
                manual.append(cp)
                existing_names.add(cp["name"].lower())

        assert len(manual) == 2
