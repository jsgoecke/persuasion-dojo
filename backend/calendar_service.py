"""
Google Calendar OAuth + meeting polling.

Architecture
────────────
  CalendarService
       │
       ├── Token management
       │       ├── get_auth_url()             → redirect user to Google consent screen
       │       ├── exchange_code(code, uri)   → swap auth code for tokens, persist to disk
       │       └── refresh_if_needed()        → auto-refresh when access token is near expiry
       │
       └── Meeting polling
               └── get_upcoming_meetings(hours_ahead) → list[Meeting]

OAuth flow (standard Google 3-legged OAuth 2.0)
────────────────────────────────────────────────
  1. Frontend calls get_auth_url() and redirects user to Google.
  2. After consent, Google redirects to redirect_uri with ?code=…
  3. Backend calls exchange_code(code, redirect_uri) to swap the code for
     access_token + refresh_token.  Tokens are written to token_path.
  4. All subsequent API calls use the access_token.  When it expires (1 h),
     refresh_if_needed() silently exchanges the refresh_token for a new one.

Token storage (JSON)
────────────────────
  {
    "access_token":  "ya29…",
    "refresh_token": "1//…",
    "expires_at":    1705315200.0,   // Unix timestamp (float)
    "token_type":    "Bearer"
  }

Meeting URL extraction
──────────────────────
  Checked in order:
    1. event.conferenceData.entryPoints[].uri
    2. Regex scan of event.description for Zoom / Meet / Teams / Webex URLs

Usage
─────
    svc = CalendarService(
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    )
    url = svc.get_auth_url(redirect_uri="http://localhost:8080/oauth/callback")
    # … user visits url, grants access, browser receives ?code=xxx …
    await svc.exchange_code(code, redirect_uri="http://localhost:8080/oauth/callback")

    meetings = await svc.get_upcoming_meetings(hours_ahead=2)
    for m in meetings:
        print(m.title, m.start_dt, m.attendee_emails)
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
_GOOGLE_WATCH_URL = f"{_GOOGLE_CALENDAR_BASE}/calendars/primary/events/watch"
_GOOGLE_CHANNELS_STOP_URL = f"{_GOOGLE_CALENDAR_BASE}/channels/stop"

_SCOPES = " ".join([
    "https://www.googleapis.com/auth/calendar.readonly",
])

# Refresh the token this many seconds before it actually expires
_REFRESH_BUFFER_S = 60

# Renew a push-watch channel this many seconds before it expires.
# Google caps channel lifetime at 7 days (604 800 s); we renew 1 h early.
_WATCH_RENEWAL_BUFFER_S = 3_600

_DEFAULT_TOKEN_PATH = Path.home() / ".persuasion_dojo_token.json"
_DEFAULT_WATCH_PATH = Path.home() / ".persuasion_dojo_watch.json"

# Regex patterns for video-call URLs inside event descriptions
_MEETING_URL_RE = re.compile(
    r"https://(?:"
    r"[\w-]+\.zoom\.us/[^\s<>\"']+"
    r"|meet\.google\.com/[^\s<>\"']+"
    r"|teams\.microsoft\.com/[^\s<>\"']+"
    r"|[\w-]+\.webex\.com/[^\s<>\"']+"
    r")",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PostFn = Callable[..., Awaitable[dict]]
GetFn = Callable[..., Awaitable[dict]]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Meeting:
    """A single calendar event."""
    id: str
    title: str
    start_dt: datetime
    end_dt: datetime
    attendee_emails: list[str] = field(default_factory=list)
    attendee_names: list[str] = field(default_factory=list)
    meeting_url: str | None = None


@dataclass
class WatchChannel:
    """
    A registered Google Calendar push-notification channel.

    Google notifies the ``address`` URL via HTTP POST whenever the user's
    primary calendar changes.  Channels expire (max 7 days) and must be
    renewed or re-registered before ``expiration_ms`` is reached.

    Attributes
    ----------
    channel_id:
        UUID chosen by the client when registering the watch.
    resource_id:
        Opaque ID returned by Google; required to stop the channel.
    expiration_ms:
        Expiry as milliseconds since the Unix epoch (Google's format).
    """
    channel_id: str
    resource_id: str
    expiration_ms: int

    @property
    def expires_at(self) -> float:
        """Expiry as a Unix timestamp (seconds)."""
        return self.expiration_ms / 1000.0

    @property
    def is_active(self) -> bool:
        """True while the channel is valid and not yet in the renewal window."""
        return time.time() < self.expires_at - _WATCH_RENEWAL_BUFFER_S

    @property
    def needs_renewal(self) -> bool:
        """True when the channel is expired or within the renewal buffer."""
        return not self.is_active


# ---------------------------------------------------------------------------
# CalendarService
# ---------------------------------------------------------------------------

class CalendarService:
    """
    Google Calendar OAuth client + meeting poller.

    Parameters
    ----------
    client_id / client_secret:
        Google OAuth 2.0 credentials from the Cloud Console.
    token_path:
        Path where the access/refresh token JSON is persisted.
    _post_fn / _get_fn:
        Injectable async HTTP functions for testing.
        ``_post_fn(url, *, headers, data) -> dict``
        ``_get_fn(url, *, headers, params) -> dict``
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        token_path: str | Path = _DEFAULT_TOKEN_PATH,
        watch_path: str | Path = _DEFAULT_WATCH_PATH,
        _post_fn: PostFn | None = None,
        _get_fn: GetFn | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_path = Path(token_path)
        self._watch_path = Path(watch_path)
        self._post_fn = _post_fn or _httpx_post
        self._get_fn = _get_fn or _httpx_get

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def get_auth_url(self, redirect_uri: str, state: str = "") -> str:
        """Return the Google consent screen URL."""
        params: dict[str, str] = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _SCOPES,
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> None:
        """
        Exchange an authorization code for access + refresh tokens and
        persist them to ``token_path``.
        """
        response = await self._post_fn(
            _GOOGLE_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        self._save_token(response)
        logger.info("CalendarService: tokens exchanged and saved")

    async def refresh_if_needed(self) -> bool:
        """
        Refresh the access token if it is within ``_REFRESH_BUFFER_S`` of
        expiry (or already expired).

        Returns ``True`` if a refresh was performed.
        Raises ``RuntimeError`` if not authenticated (no refresh token).
        """
        token = self._load_token()
        if token is None:
            raise RuntimeError("Not authenticated — call exchange_code() first")

        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("No refresh_token stored — re-authenticate")

        expires_at: float = float(token.get("expires_at", 0.0))
        if time.time() < expires_at - _REFRESH_BUFFER_S:
            return False   # still valid

        logger.info("CalendarService: refreshing access token")
        response = await self._post_fn(
            _GOOGLE_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
            },
        )

        # Google does not always return a new refresh_token — keep the old one
        if "refresh_token" not in response:
            response["refresh_token"] = refresh_token

        self._save_token(response)
        logger.info("CalendarService: access token refreshed")
        return True

    # ------------------------------------------------------------------
    # Meeting polling
    # ------------------------------------------------------------------

    async def get_upcoming_meetings(self, hours_ahead: int = 24) -> list[Meeting]:
        """
        Return calendar events starting in the next *hours_ahead* hours.

        Calls ``refresh_if_needed()`` automatically before the API request.
        """
        await self.refresh_if_needed()

        token = self._load_token()
        if token is None:
            raise RuntimeError("Not authenticated")

        access_token: str = token["access_token"]
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(hours=hours_ahead)).isoformat()

        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "50",
        }
        headers = {"Authorization": f"Bearer {access_token}"}

        response = await self._get_fn(
            f"{_GOOGLE_CALENDAR_BASE}/calendars/primary/events",
            headers=headers,
            params=params,
        )

        items: list[dict] = response.get("items", [])
        meetings: list[Meeting] = []
        for item in items:
            meeting = _parse_event(item)
            if meeting is not None:
                meetings.append(meeting)

        logger.debug("CalendarService: found %d meetings in next %dh", len(meetings), hours_ahead)
        return meetings

    # ------------------------------------------------------------------
    # Participant matching
    # ------------------------------------------------------------------

    @staticmethod
    def match_participants(
        attendee_emails: list[str],
        known_profiles: dict[str, str],
    ) -> dict[str, str]:
        """
        Match a meeting's attendee list against known participant profiles.

        Parameters
        ----------
        attendee_emails:
            List of e-mail addresses from a calendar event.
        known_profiles:
            Mapping of ``{email: name}`` for participants already in the
            database / pre-seeding store.

        Returns
        -------
        dict[str, str]
            ``{email: name}`` for each attendee that has a known profile.
            Emails are compared case-insensitively.
        """
        lowered = {k.lower(): v for k, v in known_profiles.items()}
        matched: dict[str, str] = {}
        for email in attendee_emails:
            name = lowered.get(email.lower())
            if name is not None:
                matched[email] = name
        return matched

    # ------------------------------------------------------------------
    # Push-watch management
    # ------------------------------------------------------------------

    async def register_push_watch(
        self,
        webhook_url: str,
        expiration_seconds: int = 604_800,
    ) -> WatchChannel:
        """
        Register a Google Calendar push-notification channel.

        Google will POST to *webhook_url* whenever the user's primary calendar
        changes.  The channel must be renewed before it expires (max 7 days).

        Parameters
        ----------
        webhook_url:
            Public HTTPS URL to receive ``POST`` notifications.
            Must be reachable from the internet (requires the cloud backend).
        expiration_seconds:
            Requested lifetime in seconds.  Google caps this at 604 800 (7 days).
            The actual expiry in the returned ``WatchChannel`` reflects what
            Google granted.

        Returns
        -------
        WatchChannel
            Persisted to ``watch_path`` so it survives process restarts.

        Raises
        ------
        RuntimeError
            If not authenticated.
        """
        await self.refresh_if_needed()
        token = self._load_token()
        if token is None:
            raise RuntimeError("Not authenticated — call exchange_code() first")

        channel_id = str(uuid.uuid4())
        requested_expiry_ms = int((time.time() + expiration_seconds) * 1000)

        headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "id": channel_id,
            "type": "web_hook",
            "address": webhook_url,
            "expiration": str(requested_expiry_ms),
        }

        response = await self._post_fn(
            _GOOGLE_WATCH_URL,
            headers=headers,
            json=body,
        )

        channel = WatchChannel(
            channel_id=response["id"],
            resource_id=response["resourceId"],
            expiration_ms=int(response.get("expiration", requested_expiry_ms)),
        )
        self._save_watch(channel)
        logger.info(
            "CalendarService: push watch registered channel=%s expires_at=%.0f",
            channel.channel_id, channel.expires_at,
        )
        return channel

    async def stop_push_watch(self) -> bool:
        """
        Stop the currently registered push-notification channel.

        Returns ``True`` if a channel was stopped, ``False`` if there was
        no active channel to stop.
        """
        channel = self._load_watch()
        if channel is None:
            return False

        await self.refresh_if_needed()
        token = self._load_token()
        if token is None:
            raise RuntimeError("Not authenticated")

        headers = {
            "Authorization": f"Bearer {token['access_token']}",
            "Content-Type": "application/json",
        }
        body = {
            "id": channel.channel_id,
            "resourceId": channel.resource_id,
        }
        await self._post_fn(
            _GOOGLE_CHANNELS_STOP_URL,
            headers=headers,
            json=body,
        )

        self._delete_watch()
        logger.info(
            "CalendarService: push watch stopped channel=%s",
            channel.channel_id,
        )
        return True

    @property
    def active_watch(self) -> WatchChannel | None:
        """Return the persisted ``WatchChannel``, or ``None`` if absent."""
        return self._load_watch()

    @property
    def is_watch_active(self) -> bool:
        """True if a push-watch channel is registered and not near expiry."""
        ch = self._load_watch()
        return ch is not None and ch.is_active

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        """True if a token file exists that contains a refresh_token."""
        token = self._load_token()
        return token is not None and bool(token.get("refresh_token"))

    # ------------------------------------------------------------------
    # Token persistence
    # ------------------------------------------------------------------

    def _load_token(self) -> dict | None:
        """Read token from disk.  Returns ``None`` if file absent or corrupt."""
        if not self._token_path.exists():
            return None
        try:
            return json.loads(self._token_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("CalendarService: failed to load token — %s", exc)
            return None

    def _save_token(self, token_response: dict) -> None:
        """
        Normalise a Google token response and write it to disk.

        Google returns ``expires_in`` (seconds); we convert to an absolute
        Unix timestamp (``expires_at``) so expiry checks are clock-based.
        """
        token: dict[str, Any] = dict(token_response)
        expires_in: int = int(token.pop("expires_in", 3600))
        token["expires_at"] = time.time() + expires_in
        self._token_path.write_text(json.dumps(token))

    def _load_watch(self) -> WatchChannel | None:
        """Read watch state from disk.  Returns ``None`` if absent or corrupt."""
        if not self._watch_path.exists():
            return None
        try:
            data = json.loads(self._watch_path.read_text())
            return WatchChannel(
                channel_id=data["channel_id"],
                resource_id=data["resource_id"],
                expiration_ms=int(data["expiration_ms"]),
            )
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("CalendarService: failed to load watch state — %s", exc)
            return None

    def _save_watch(self, channel: WatchChannel) -> None:
        """Persist a ``WatchChannel`` to disk."""
        self._watch_path.write_text(json.dumps({
            "channel_id": channel.channel_id,
            "resource_id": channel.resource_id,
            "expiration_ms": channel.expiration_ms,
        }))

    def _delete_watch(self) -> None:
        """Remove the persisted watch state."""
        self._watch_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Event parsing helpers
# ---------------------------------------------------------------------------

def _parse_event(item: dict) -> Meeting | None:
    """Convert a raw Google Calendar event dict to a ``Meeting``.  Returns
    ``None`` for cancelled events or events with no useful time data."""
    if item.get("status") == "cancelled":
        return None

    start_raw = item.get("start", {})
    end_raw = item.get("end", {})

    start_dt = _parse_dt(start_raw)
    end_dt = _parse_dt(end_raw)
    if start_dt is None or end_dt is None:
        return None

    raw_attendees = [
        a for a in item.get("attendees", [])
        if "email" in a and not a.get("self", False)
    ]
    attendees: list[str] = [a["email"] for a in raw_attendees]
    # Google Calendar includes displayName when available; fall back to email local part
    attendee_names: list[str] = [
        a.get("displayName") or a["email"].split("@")[0].replace(".", " ").title()
        for a in raw_attendees
    ]

    meeting_url = _extract_meeting_url(item)

    return Meeting(
        id=item.get("id", ""),
        title=item.get("summary", "(No title)"),
        start_dt=start_dt,
        end_dt=end_dt,
        attendee_emails=attendees,
        attendee_names=attendee_names,
        meeting_url=meeting_url,
    )


def _parse_dt(dt_block: dict) -> datetime | None:
    """Parse a Google dateTime or date block into a timezone-aware datetime."""
    if "dateTime" in dt_block:
        try:
            return datetime.fromisoformat(dt_block["dateTime"])
        except ValueError:
            return None
    if "date" in dt_block:
        # All-day event — treat as midnight UTC
        try:
            d = datetime.strptime(dt_block["date"], "%Y-%m-%d")
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _extract_meeting_url(item: dict) -> str | None:
    """Pull a video-call URL from conferenceData or the event description."""
    conf = item.get("conferenceData", {})
    for entry in conf.get("entryPoints", []):
        uri = entry.get("uri", "")
        if uri.startswith("https://"):
            return uri

    description: str = item.get("description") or ""
    match = _MEETING_URL_RE.search(description)
    if match:
        return match.group(0)
    return None


# ---------------------------------------------------------------------------
# Default HTTP implementations (httpx)
# ---------------------------------------------------------------------------

async def _httpx_post(
    url: str,
    *,
    headers: dict,
    data: dict | None = None,
    json: dict | None = None,
) -> dict:
    """Send a POST request with either form-encoded ``data`` or JSON body.

    Exactly one of ``data`` / ``json`` must be provided.
    Token-exchange and refresh calls pass ``data``; watch and stop calls
    pass ``json``.
    """
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, data=data, json=json)
        resp.raise_for_status()
        # Google channels/stop returns 204 No Content — handle empty body.
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


async def _httpx_get(url: str, *, headers: dict, params: dict) -> dict:
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()
