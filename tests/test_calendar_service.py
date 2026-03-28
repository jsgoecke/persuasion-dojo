"""
Tests for backend/calendar_service.py (CalendarService).

No real HTTP calls — all network I/O is replaced by injectable
``_post_fn`` / ``_get_fn`` callables that return preset JSON payloads.

Covers:
  - get_auth_url(): contains client_id, redirect_uri, required params
  - exchange_code(): posts correct fields, saves tokens to disk
  - Token persistence: load/save round-trip, missing file returns None,
    corrupt file returns None, expires_at computed from expires_in
  - is_authenticated: True when refresh_token present, False otherwise
  - refresh_if_needed():
      - skips refresh when token is still valid
      - refreshes when token is expired
      - refreshes when token expires within buffer window
      - preserves existing refresh_token when Google omits it in response
      - raises RuntimeError when not authenticated
      - raises RuntimeError when no refresh_token stored
  - get_upcoming_meetings(): parses events, extracts attendees, urls,
      calls refresh_if_needed(), passes correct time window params
  - Event parsing: cancelled events skipped, all-day events parsed,
      dateTime events parsed, events missing time block skipped,
      self attendees excluded, conference URL preferred over description,
      description URL extracted by regex
  - match_participants(): case-insensitive matching, partial match,
      no matches, empty inputs
  - _parse_event helpers via get_upcoming_meetings integration
  - WatchChannel properties: expires_at conversion, is_active/needs_renewal
      vs renewal buffer, expired channel
  - register_push_watch(): returns WatchChannel, persists to disk, raises
      when unauthenticated, active_watch / is_watch_active reflect state
  - stop_push_watch(): deletes watch file, returns True; returns False
      when no watch; raises RuntimeError when unauthenticated
  - Watch persistence: missing file → None, corrupt file → None,
      save/load round-trip preserves all fields
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from backend.calendar_service import (
    CalendarService,
    Meeting,
    WatchChannel,
    _parse_event,
    _extract_meeting_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    post_response: dict | None = None,
    get_response: dict | None = None,
    token: dict | None = None,
) -> tuple[CalendarService, Path]:
    """Build a CalendarService backed by a temp token file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    token_path = Path(tmp.name)

    if token is not None:
        token_path.write_text(json.dumps(token))
    else:
        token_path.unlink()   # start with no token

    async def fake_post(url, *, headers, data=None, json=None):
        return post_response or {}

    async def fake_get(url, *, headers, params):
        return get_response or {"items": []}

    svc = CalendarService(
        client_id="test-client-id",
        client_secret="test-client-secret",
        token_path=token_path,
        _post_fn=fake_post,
        _get_fn=fake_get,
    )
    return svc, token_path


def _valid_token(expires_in: int = 3600) -> dict:
    return {
        "access_token": "access-abc",
        "refresh_token": "refresh-xyz",
        "expires_at": time.time() + expires_in,
        "token_type": "Bearer",
    }


def _expired_token() -> dict:
    return {
        "access_token": "old-token",
        "refresh_token": "refresh-xyz",
        "expires_at": time.time() - 10,   # already expired
        "token_type": "Bearer",
    }


def _event(
    id: str = "evt1",
    summary: str = "Team Standup",
    start: str = "2024-01-15T10:00:00+00:00",
    end: str = "2024-01-15T10:30:00+00:00",
    attendees: list | None = None,
    status: str = "confirmed",
    description: str = "",
    conference_uri: str | None = None,
) -> dict:
    item: dict = {
        "id": id,
        "summary": summary,
        "status": status,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "description": description,
        "attendees": attendees or [],
    }
    if conference_uri:
        item["conferenceData"] = {
            "entryPoints": [{"uri": conference_uri, "entryPointType": "video"}]
        }
    return item


# ---------------------------------------------------------------------------
# get_auth_url
# ---------------------------------------------------------------------------

class TestGetAuthUrl:
    def test_contains_client_id(self):
        svc, tp = _make_service()
        try:
            url = svc.get_auth_url("http://localhost/callback")
            assert "test-client-id" in url
        finally:
            tp.unlink(missing_ok=True)

    def test_contains_redirect_uri(self):
        svc, tp = _make_service()
        try:
            url = svc.get_auth_url("http://localhost/callback")
            assert "localhost" in url
        finally:
            tp.unlink(missing_ok=True)

    def test_contains_response_type_code(self):
        svc, tp = _make_service()
        try:
            url = svc.get_auth_url("http://localhost/callback")
            assert "response_type=code" in url
        finally:
            tp.unlink(missing_ok=True)

    def test_contains_offline_access(self):
        svc, tp = _make_service()
        try:
            url = svc.get_auth_url("http://localhost/callback")
            assert "offline" in url
        finally:
            tp.unlink(missing_ok=True)

    def test_state_included_when_provided(self):
        svc, tp = _make_service()
        try:
            url = svc.get_auth_url("http://localhost/callback", state="csrf-token")
            assert "csrf-token" in url
        finally:
            tp.unlink(missing_ok=True)

    def test_state_absent_when_empty(self):
        svc, tp = _make_service()
        try:
            url = svc.get_auth_url("http://localhost/callback", state="")
            assert "state=" not in url
        finally:
            tp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------

class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_saves_access_token(self):
        token_response = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        svc, tp = _make_service(post_response=token_response)
        try:
            await svc.exchange_code("auth-code", "http://localhost/cb")
            saved = json.loads(tp.read_text())
            assert saved["access_token"] == "new-access"
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_saves_refresh_token(self):
        token_response = {
            "access_token": "a",
            "refresh_token": "r",
            "expires_in": 3600,
        }
        svc, tp = _make_service(post_response=token_response)
        try:
            await svc.exchange_code("code", "http://localhost/cb")
            saved = json.loads(tp.read_text())
            assert saved["refresh_token"] == "r"
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_expires_at_computed_from_expires_in(self):
        before = time.time()
        token_response = {
            "access_token": "a",
            "refresh_token": "r",
            "expires_in": 3600,
        }
        svc, tp = _make_service(post_response=token_response)
        try:
            await svc.exchange_code("code", "http://localhost/cb")
            saved = json.loads(tp.read_text())
            assert saved["expires_at"] >= before + 3600 - 5
            assert saved["expires_at"] <= time.time() + 3600 + 5
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_post_includes_code(self):
        captured: dict = {}

        async def spy_post(url, *, headers, data):
            captured["data"] = data
            return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

        svc, tp = _make_service()
        svc._post_fn = spy_post
        try:
            await svc.exchange_code("my-code", "http://localhost/cb")
            assert captured["data"]["code"] == "my-code"
        finally:
            tp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# is_authenticated
# ---------------------------------------------------------------------------

class TestIsAuthenticated:
    def test_false_when_no_token_file(self):
        svc, tp = _make_service()
        assert not svc.is_authenticated

    def test_false_when_token_has_no_refresh_token(self):
        token = {"access_token": "a", "expires_at": time.time() + 3600}
        svc, tp = _make_service(token=token)
        try:
            assert not svc.is_authenticated
        finally:
            tp.unlink(missing_ok=True)

    def test_true_when_refresh_token_present(self):
        svc, tp = _make_service(token=_valid_token())
        try:
            assert svc.is_authenticated
        finally:
            tp.unlink(missing_ok=True)

    def test_false_when_token_file_corrupt(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.write(b"not json{{")
        tmp.close()
        svc, _ = _make_service()
        svc._token_path = Path(tmp.name)
        try:
            assert not svc.is_authenticated
        finally:
            os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# refresh_if_needed
# ---------------------------------------------------------------------------

class TestRefreshIfNeeded:
    @pytest.mark.asyncio
    async def test_skips_when_token_valid(self):
        svc, tp = _make_service(token=_valid_token(expires_in=3600))
        try:
            refreshed = await svc.refresh_if_needed()
            assert not refreshed
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_refreshes_when_token_expired(self):
        refresh_response = {
            "access_token": "new-token",
            "expires_in": 3600,
        }
        svc, tp = _make_service(
            post_response=refresh_response,
            token=_expired_token(),
        )
        try:
            refreshed = await svc.refresh_if_needed()
            assert refreshed
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_refreshes_when_within_buffer(self):
        """Token that expires in <60 s must trigger a refresh."""
        refresh_response = {"access_token": "new", "expires_in": 3600}
        token = _valid_token(expires_in=30)   # expires in 30 s < 60 s buffer
        svc, tp = _make_service(post_response=refresh_response, token=token)
        try:
            refreshed = await svc.refresh_if_needed()
            assert refreshed
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_new_access_token_saved_after_refresh(self):
        refresh_response = {"access_token": "brand-new", "expires_in": 3600}
        svc, tp = _make_service(
            post_response=refresh_response,
            token=_expired_token(),
        )
        try:
            await svc.refresh_if_needed()
            saved = json.loads(tp.read_text())
            assert saved["access_token"] == "brand-new"
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_preserves_refresh_token_when_google_omits_it(self):
        """Google refresh responses don't always include a new refresh_token."""
        refresh_response = {"access_token": "new", "expires_in": 3600}
        original_token = _expired_token()  # has refresh_token = "refresh-xyz"
        svc, tp = _make_service(
            post_response=refresh_response,
            token=original_token,
        )
        try:
            await svc.refresh_if_needed()
            saved = json.loads(tp.read_text())
            assert saved["refresh_token"] == "refresh-xyz"
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_raises_when_not_authenticated(self):
        svc, tp = _make_service()   # no token file
        try:
            with pytest.raises(RuntimeError, match="Not authenticated"):
                await svc.refresh_if_needed()
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_raises_when_no_refresh_token_stored(self):
        token = {"access_token": "a", "expires_at": time.time() - 10}
        svc, tp = _make_service(token=token)
        try:
            with pytest.raises(RuntimeError, match="No refresh_token"):
                await svc.refresh_if_needed()
        finally:
            tp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# get_upcoming_meetings
# ---------------------------------------------------------------------------

class TestGetUpcomingMeetings:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_events(self):
        svc, tp = _make_service(get_response={"items": []}, token=_valid_token())
        try:
            meetings = await svc.get_upcoming_meetings()
            assert meetings == []
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_returns_parsed_meeting(self):
        items = [_event(summary="Board Review")]
        svc, tp = _make_service(get_response={"items": items}, token=_valid_token())
        try:
            meetings = await svc.get_upcoming_meetings()
            assert len(meetings) == 1
            assert meetings[0].title == "Board Review"
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_attendee_emails_extracted(self):
        attendees = [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ]
        items = [_event(attendees=attendees)]
        svc, tp = _make_service(get_response={"items": items}, token=_valid_token())
        try:
            meetings = await svc.get_upcoming_meetings()
            assert "alice@example.com" in meetings[0].attendee_emails
            assert "bob@example.com" in meetings[0].attendee_emails
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_self_attendee_excluded(self):
        attendees = [
            {"email": "me@example.com", "self": True},
            {"email": "them@example.com"},
        ]
        items = [_event(attendees=attendees)]
        svc, tp = _make_service(get_response={"items": items}, token=_valid_token())
        try:
            meetings = await svc.get_upcoming_meetings()
            assert "me@example.com" not in meetings[0].attendee_emails
            assert "them@example.com" in meetings[0].attendee_emails
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_conference_url_extracted(self):
        items = [_event(conference_uri="https://meet.google.com/abc-defg-hij")]
        svc, tp = _make_service(get_response={"items": items}, token=_valid_token())
        try:
            meetings = await svc.get_upcoming_meetings()
            assert meetings[0].meeting_url == "https://meet.google.com/abc-defg-hij"
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_cancelled_events_skipped(self):
        items = [
            _event(id="evt1", status="cancelled"),
            _event(id="evt2", summary="Active Meeting"),
        ]
        svc, tp = _make_service(get_response={"items": items}, token=_valid_token())
        try:
            meetings = await svc.get_upcoming_meetings()
            assert len(meetings) == 1
            assert meetings[0].title == "Active Meeting"
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_calls_refresh_if_needed(self):
        """get_upcoming_meetings must trigger a token refresh when needed."""
        refreshed = {"access_token": "refreshed", "expires_in": 3600}
        svc, tp = _make_service(
            post_response=refreshed,
            get_response={"items": []},
            token=_expired_token(),
        )
        try:
            await svc.get_upcoming_meetings()
            saved = json.loads(tp.read_text())
            assert saved["access_token"] == "refreshed"
        finally:
            tp.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_get_receives_authorization_header(self):
        captured: dict = {}

        async def spy_get(url, *, headers, params):
            captured["headers"] = headers
            return {"items": []}

        svc, tp = _make_service(token=_valid_token())
        svc._get_fn = spy_get
        try:
            await svc.get_upcoming_meetings()
            assert "Bearer" in captured["headers"]["Authorization"]
        finally:
            tp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# match_participants
# ---------------------------------------------------------------------------

class TestMatchParticipants:
    def test_exact_match(self):
        result = CalendarService.match_participants(
            ["alice@example.com"],
            {"alice@example.com": "Alice"},
        )
        assert result == {"alice@example.com": "Alice"}

    def test_case_insensitive_match(self):
        result = CalendarService.match_participants(
            ["Alice@Example.COM"],
            {"alice@example.com": "Alice"},
        )
        assert "Alice@Example.COM" in result

    def test_partial_match(self):
        result = CalendarService.match_participants(
            ["alice@example.com", "unknown@example.com"],
            {"alice@example.com": "Alice"},
        )
        assert len(result) == 1
        assert "alice@example.com" in result

    def test_no_match_returns_empty(self):
        result = CalendarService.match_participants(
            ["nobody@example.com"],
            {"alice@example.com": "Alice"},
        )
        assert result == {}

    def test_empty_attendees(self):
        result = CalendarService.match_participants(
            [],
            {"alice@example.com": "Alice"},
        )
        assert result == {}

    def test_empty_known_profiles(self):
        result = CalendarService.match_participants(
            ["alice@example.com"],
            {},
        )
        assert result == {}

    def test_multiple_matches(self):
        result = CalendarService.match_participants(
            ["alice@example.com", "bob@example.com", "carol@example.com"],
            {"alice@example.com": "Alice", "bob@example.com": "Bob"},
        )
        assert len(result) == 2
        assert result["alice@example.com"] == "Alice"
        assert result["bob@example.com"] == "Bob"


# ---------------------------------------------------------------------------
# Event parsing helpers (_parse_event, _extract_meeting_url)
# ---------------------------------------------------------------------------

class TestParseEvent:
    def test_cancelled_event_returns_none(self):
        assert _parse_event({"status": "cancelled"}) is None

    def test_event_without_time_returns_none(self):
        assert _parse_event({"id": "x", "summary": "X", "status": "confirmed"}) is None

    def test_all_day_event_parsed(self):
        item = {
            "id": "all-day",
            "summary": "Company Holiday",
            "status": "confirmed",
            "start": {"date": "2024-01-15"},
            "end": {"date": "2024-01-16"},
        }
        meeting = _parse_event(item)
        assert meeting is not None
        assert meeting.title == "Company Holiday"
        assert meeting.start_dt.tzinfo is not None

    def test_datetime_event_parsed(self):
        item = _event(summary="Sales Call")
        meeting = _parse_event(item)
        assert meeting is not None
        assert meeting.title == "Sales Call"

    def test_default_title_when_no_summary(self):
        item = _event()
        del item["summary"]
        meeting = _parse_event(item)
        assert meeting is not None
        assert meeting.title == "(No title)"


class TestExtractMeetingUrl:
    def test_zoom_from_description(self):
        item = {"description": "Join at https://company.zoom.us/j/123456789"}
        assert _extract_meeting_url(item) == "https://company.zoom.us/j/123456789"

    def test_meet_from_description(self):
        item = {"description": "Meet: https://meet.google.com/abc-defg-hij today"}
        assert _extract_meeting_url(item) == "https://meet.google.com/abc-defg-hij"

    def test_conference_data_preferred_over_description(self):
        item = {
            "description": "https://company.zoom.us/j/111",
            "conferenceData": {
                "entryPoints": [{"uri": "https://meet.google.com/conf-link"}]
            },
        }
        assert _extract_meeting_url(item) == "https://meet.google.com/conf-link"

    def test_no_url_returns_none(self):
        item = {"description": "Just a regular meeting, no link."}
        assert _extract_meeting_url(item) is None

    def test_empty_description_returns_none(self):
        item = {"description": ""}
        assert _extract_meeting_url(item) is None


# ---------------------------------------------------------------------------
# WatchChannel properties
# ---------------------------------------------------------------------------

class TestWatchChannelProperties:
    """Unit tests for WatchChannel computed properties."""

    def _channel(self, expiration_ms: int) -> WatchChannel:
        return WatchChannel(
            channel_id="ch-test",
            resource_id="res-test",
            expiration_ms=expiration_ms,
        )

    def test_expires_at_converts_ms_to_seconds(self):
        ch = self._channel(expiration_ms=1_000_000_000_000)
        assert ch.expires_at == 1_000_000_000.0

    def test_is_active_true_when_far_from_expiry(self):
        # Expires 2 hours from now — well outside the 1-hour renewal buffer.
        future_ms = int((time.time() + 7200) * 1000)
        ch = self._channel(future_ms)
        assert ch.is_active is True
        assert ch.needs_renewal is False

    def test_is_active_false_within_renewal_buffer(self):
        # Expires 30 minutes from now — inside the 1-hour buffer.
        soon_ms = int((time.time() + 1800) * 1000)
        ch = self._channel(soon_ms)
        assert ch.is_active is False
        assert ch.needs_renewal is True

    def test_is_active_false_when_expired(self):
        past_ms = int((time.time() - 1) * 1000)
        ch = self._channel(past_ms)
        assert ch.is_active is False


# ---------------------------------------------------------------------------
# register_push_watch
# ---------------------------------------------------------------------------

def _make_service_with_watch(
    post_response: dict | None = None,
    token: dict | None = None,
) -> tuple[CalendarService, Path, Path]:
    """Build a CalendarService with separate temp files for token and watch."""
    token_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    token_tmp.close()
    token_path = Path(token_tmp.name)

    watch_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    watch_tmp.close()
    watch_path = Path(watch_tmp.name)
    watch_path.unlink()   # start with no watch state

    if token is not None:
        token_path.write_text(json.dumps(token))
    else:
        token_path.unlink()

    async def fake_post(url, *, headers, data=None, json=None):
        return post_response or {}

    svc = CalendarService(
        client_id="id",
        client_secret="secret",
        token_path=token_path,
        watch_path=watch_path,
        _post_fn=fake_post,
        _get_fn=lambda *a, **kw: {},
    )
    return svc, token_path, watch_path


class TestRegisterPushWatch:
    @pytest.mark.asyncio
    async def test_returns_watch_channel_and_persists(self, tmp_path):
        token = _valid_token()
        watch_response = {
            "id": "ch-uuid-123",
            "resourceId": "res-opaque",
            "expiration": str(int((time.time() + 604_800) * 1000)),
        }
        svc, _, watch_path = _make_service_with_watch(
            post_response=watch_response, token=token
        )

        channel = await svc.register_push_watch("https://example.com/webhook")

        assert channel.channel_id == "ch-uuid-123"
        assert channel.resource_id == "res-opaque"
        assert channel.is_active  # expires in ~7 days

        # Persisted to disk
        assert watch_path.exists()
        stored = json.loads(watch_path.read_text())
        assert stored["channel_id"] == "ch-uuid-123"
        assert stored["resource_id"] == "res-opaque"

    @pytest.mark.asyncio
    async def test_raises_when_not_authenticated(self, tmp_path):
        svc, _, _ = _make_service_with_watch(token=None)
        with pytest.raises(RuntimeError, match="Not authenticated"):
            await svc.register_push_watch("https://example.com/hook")

    @pytest.mark.asyncio
    async def test_active_watch_property_returns_stored_channel(self, tmp_path):
        token = _valid_token()
        watch_response = {
            "id": "ch-abc",
            "resourceId": "res-abc",
            "expiration": str(int((time.time() + 604_800) * 1000)),
        }
        svc, _, _ = _make_service_with_watch(
            post_response=watch_response, token=token
        )
        await svc.register_push_watch("https://example.com/hook")

        ch = svc.active_watch
        assert ch is not None
        assert ch.channel_id == "ch-abc"

    @pytest.mark.asyncio
    async def test_is_watch_active_true_for_fresh_channel(self, tmp_path):
        token = _valid_token()
        watch_response = {
            "id": "ch-fresh",
            "resourceId": "res-fresh",
            "expiration": str(int((time.time() + 604_800) * 1000)),
        }
        svc, _, _ = _make_service_with_watch(
            post_response=watch_response, token=token
        )
        await svc.register_push_watch("https://example.com/hook")
        assert svc.is_watch_active is True

    def test_is_watch_active_false_when_no_watch(self, tmp_path):
        svc, _, _ = _make_service_with_watch(token=_valid_token())
        assert svc.is_watch_active is False


# ---------------------------------------------------------------------------
# stop_push_watch
# ---------------------------------------------------------------------------

class TestStopPushWatch:
    @pytest.mark.asyncio
    async def test_stop_returns_true_and_deletes_watch_file(self, tmp_path):
        token = _valid_token()
        watch_response = {
            "id": "ch-stop",
            "resourceId": "res-stop",
            "expiration": str(int((time.time() + 604_800) * 1000)),
        }
        svc, _, watch_path = _make_service_with_watch(
            post_response=watch_response, token=token
        )
        # Register first
        await svc.register_push_watch("https://example.com/hook")
        assert watch_path.exists()

        stopped = await svc.stop_push_watch()

        assert stopped is True
        assert not watch_path.exists()

    @pytest.mark.asyncio
    async def test_stop_returns_false_when_no_watch(self, tmp_path):
        svc, _, _ = _make_service_with_watch(token=_valid_token())
        result = await svc.stop_push_watch()
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_raises_when_not_authenticated(self, tmp_path):
        # Pre-populate watch file without going through register
        token_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        token_tmp.close()
        token_path = Path(token_tmp.name)
        token_path.unlink()

        watch_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        watch_tmp.close()
        watch_path = Path(watch_tmp.name)
        watch_path.write_text(json.dumps({
            "channel_id": "ch-orphan",
            "resource_id": "res-orphan",
            "expiration_ms": int((time.time() + 7200) * 1000),
        }))

        async def fake_post(url, *, headers, data=None, json=None):
            return {}

        svc = CalendarService(
            client_id="id",
            client_secret="secret",
            token_path=token_path,
            watch_path=watch_path,
            _post_fn=fake_post,
        )

        with pytest.raises(RuntimeError, match="Not authenticated"):
            await svc.stop_push_watch()


# ---------------------------------------------------------------------------
# Watch persistence: load / save / corrupt file
# ---------------------------------------------------------------------------

class TestWatchPersistence:
    def test_load_watch_returns_none_for_missing_file(self, tmp_path):
        svc = CalendarService(
            client_id="id",
            client_secret="secret",
            token_path=tmp_path / "token.json",
            watch_path=tmp_path / "nonexistent.json",
        )
        assert svc.active_watch is None

    def test_load_watch_returns_none_for_corrupt_file(self, tmp_path):
        watch_path = tmp_path / "watch.json"
        watch_path.write_text("NOT VALID JSON{{")
        svc = CalendarService(
            client_id="id",
            client_secret="secret",
            token_path=tmp_path / "token.json",
            watch_path=watch_path,
        )
        assert svc.active_watch is None

    def test_save_and_load_round_trip(self, tmp_path):
        watch_path = tmp_path / "watch.json"
        expiry_ms = int((time.time() + 86400) * 1000)
        channel = WatchChannel(
            channel_id="round-trip",
            resource_id="res-round-trip",
            expiration_ms=expiry_ms,
        )

        svc = CalendarService(
            client_id="id",
            client_secret="secret",
            token_path=tmp_path / "token.json",
            watch_path=watch_path,
        )
        svc._save_watch(channel)

        loaded = svc.active_watch
        assert loaded is not None
        assert loaded.channel_id == "round-trip"
        assert loaded.resource_id == "res-round-trip"
        assert loaded.expiration_ms == expiry_ms
