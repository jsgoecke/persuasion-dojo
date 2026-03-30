"""
LinkedIn public profile scraper.

Extracts name, headline, and summary from a LinkedIn profile URL using
publicly available OpenGraph meta tags and JSON-LD structured data.
No authentication required — only reads what LinkedIn renders for
unauthenticated visitors (link previews, search engines).

Usage:
    text = await fetch_linkedin_profile("https://www.linkedin.com/in/satyanadella")
    # → "Satya Nadella\nChairman and CEO at Microsoft\n..."
"""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser

import httpx

logger = logging.getLogger(__name__)

_LINKEDIN_URL_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/in/[\w\-]+/?$", re.IGNORECASE
)

# Headers that mimic a standard Chrome browser request.
# LinkedIn verifies Googlebot claims via reverse DNS — a Googlebot UA from a
# non-Google IP gets rejected. Use a real browser UA instead.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_linkedin_url(text: str) -> bool:
    """Return True if *text* looks like a LinkedIn profile URL."""
    return bool(_LINKEDIN_URL_RE.match(text.strip()))


class _MetaParser(HTMLParser):
    """Lightweight HTML parser that extracts <meta> og: tags and JSON-LD."""

    def __init__(self) -> None:
        super().__init__()
        self.og: dict[str, str] = {}
        self.json_ld: list[dict] = []
        self._in_script = False
        self._script_buf = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            a = dict(attrs)
            prop = a.get("property", "") or a.get("name", "")
            content = a.get("content", "")
            if prop.startswith("og:") and content:
                self.og[prop] = content
        elif tag == "script":
            a = dict(attrs)
            if a.get("type") == "application/ld+json":
                self._in_script = True
                self._script_buf = ""

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_buf += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_script:
            self._in_script = False
            try:
                self.json_ld.append(json.loads(self._script_buf))
            except (json.JSONDecodeError, ValueError):
                pass


def _extract_from_html(html: str) -> dict[str, str]:
    """Parse HTML and return {name, headline, summary} from meta/JSON-LD."""
    parser = _MetaParser()
    parser.feed(html)

    name = parser.og.get("og:title", "").strip()
    description = parser.og.get("og:description", "").strip()

    # JSON-LD often has richer data
    summary = ""
    for ld in parser.json_ld:
        if ld.get("@type") == "Person":
            name = name or ld.get("name", "")
            summary = ld.get("description", "") or ld.get("jobTitle", "")
            break

    # LinkedIn og:title often includes " | LinkedIn" suffix
    name = re.sub(r"\s*[|\-–—]\s*LinkedIn\s*$", "", name).strip()

    # og:description often has "headline · location · connections"
    headline = ""
    if description:
        # Take the first sentence/segment as headline
        parts = re.split(r"\s[·•|]\s", description)
        headline = parts[0].strip() if parts else description

    return {"name": name, "headline": headline, "summary": summary or description}


async def fetch_linkedin_profile(url: str) -> str:
    """
    Fetch a LinkedIn profile URL and return extracted text suitable for
    the pre-seed classifier.

    Returns a multi-line string: name, headline, summary.
    Raises ValueError if the URL is not a LinkedIn profile or fetch fails.
    """
    url = url.strip()
    if not _LINKEDIN_URL_RE.match(url):
        raise ValueError(f"Not a LinkedIn profile URL: {url}")

    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()

    # LinkedIn redirects browser UAs to auth walls for private/restricted profiles.
    final_url = str(resp.url)
    if "/authwall" in final_url or "/login" in final_url:
        raise ValueError("LinkedIn requires authentication for this profile")

    # Guard against SSRF: after redirects, final URL must still be linkedin.com.
    if not _LINKEDIN_URL_RE.match(final_url.split("?")[0]):
        raise ValueError("Redirect led outside LinkedIn — request blocked")

    data = _extract_from_html(resp.text)

    if not data["name"] and not data["headline"]:
        raise ValueError("Could not extract profile data — LinkedIn may require authentication for this profile")

    parts = [p for p in [data["name"], data["headline"], data["summary"]] if p]
    return "\n".join(parts)


def extract_name_from_linkedin(url: str, html: str) -> str | None:
    """Extract just the person's name from LinkedIn HTML. Returns None on failure."""
    data = _extract_from_html(html)
    return data["name"] or None
