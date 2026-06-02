from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from html import unescape
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Good enough for provider URLs in Calendar location/description fields.
# We intentionally stop before HTML delimiters and quotes.
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ").,;]}>"


@dataclass(frozen=True)
class NormalizedEvent:
    """Small event shape used by scheduling and Slack rendering.

    Raw Google Calendar event objects vary by provider and by event status. This
    class keeps only fields the bot actually needs.
    """

    id: str
    status: str
    title: str
    start: datetime | None
    end: datetime | None
    is_all_day: bool
    join_url: str | None
    location: str | None
    calendar_url: str | None
    updated: str | None
    provider: str | None
    dial_in_uri: str | None = None
    creator_email: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("start", "end"):
            value = data[key]
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data


def normalize_event(raw_event: dict[str, Any]) -> NormalizedEvent:
    """Normalize a Google Calendar event into the bot's internal event shape.

    Handles Google Meet, Zoom/third-party conferenceData, location-only events,
    description-only links, all-day events, and cancelled events.
    """

    event_id = raw_event.get("id")
    if not event_id:
        raise ValueError("Google Calendar event is missing required id")

    status = raw_event.get("status", "confirmed")

    # Deleted/cancelled events from incremental sync may only contain id/status.
    if status == "cancelled":
        return NormalizedEvent(
            id=event_id,
            status=status,
            title=raw_event.get("summary") or "(cancelled)",
            start=None,
            end=None,
            is_all_day=False,
            join_url=None,
            location=None,
            calendar_url=raw_event.get("htmlLink"),
            updated=raw_event.get("updated"),
            provider=None,
            dial_in_uri=None,
        )

    start_obj = raw_event.get("start") or {}
    end_obj = raw_event.get("end") or {}
    is_all_day = "date" in start_obj and "dateTime" not in start_obj

    start = parse_google_event_datetime(start_obj)
    end = parse_google_event_datetime(end_obj)
    if start is None:
        raise ValueError(f"Google Calendar event {event_id!r} is missing start.dateTime/start.date")

    conference_data = raw_event.get("conferenceData") or {}
    solution = conference_data.get("conferenceSolution") or {}
    key = solution.get("key") or {}
    provider = solution.get("name") or key.get("type")

    return NormalizedEvent(
        id=event_id,
        status=status,
        title=raw_event.get("summary") or "(no title)",
        start=start,
        end=end,
        is_all_day=is_all_day,
        join_url=extract_join_url(raw_event),
        location=raw_event.get("location"),
        calendar_url=raw_event.get("htmlLink"),
        updated=raw_event.get("updated"),
        provider=provider,
        dial_in_uri=extract_dial_in_uri(raw_event),
        creator_email=(raw_event.get("creator") or {}).get("email"),
    )


def parse_google_event_datetime(value: dict[str, Any]) -> datetime | None:
    """Parse a Google Calendar start/end object.

    Timed events contain dateTime. All-day events contain date. For all-day
    events, this returns midnight UTC for the date so callers can compare it,
    but reminder schedulers should usually skip all-day events.
    """

    if not value:
        return None

    if value.get("dateTime"):
        return parse_rfc3339_datetime(value["dateTime"])

    if value.get("date"):
        parsed_date = date.fromisoformat(value["date"])
        return datetime.combine(parsed_date, time.min, tzinfo=timezone.utc)

    return None


def parse_rfc3339_datetime(value: str) -> datetime:
    """Parse RFC3339-ish datetimes returned by Google Calendar.

    Python's datetime.fromisoformat accepts offsets like -05:00 but not a bare
    trailing Z on older versions, so normalize Z to +00:00.
    """

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def extract_join_url(event: dict[str, Any]) -> str | None:
    """Return the best clickable meeting URL for Meet, Zoom, Teams, etc."""

    conference_data = event.get("conferenceData") or {}
    entry_points = conference_data.get("entryPoints") or []

    # 1. Prefer the explicit video conference URL.
    for entry in entry_points:
        uri = entry.get("uri")
        if entry.get("entryPointType") == "video" and is_http_url(uri):
            return clean_url(uri)

    # 2. Then any other HTTP(S) conference entry point.
    for entry in entry_points:
        uri = entry.get("uri")
        if is_http_url(uri):
            return clean_url(uri)

    # 3. Google Meet often exposes this top-level field.
    hangout_link = event.get("hangoutLink")
    if is_http_url(hangout_link):
        return clean_url(hangout_link)

    # 4. Third-party providers sometimes put the URL in location or description.
    for field in ("location", "description"):
        found = first_url(event.get(field))
        if found:
            return found

    # 5. Last resort: Calendar event details page. Useful for debugging but not
    # a direct meeting join URL.
    html_link = event.get("htmlLink")
    if is_http_url(html_link):
        return clean_url(html_link)

    return None


def extract_dial_in_uri(event: dict[str, Any]) -> str | None:
    conference_data = event.get("conferenceData") or {}
    for entry in conference_data.get("entryPoints") or []:
        uri = entry.get("uri")
        if entry.get("entryPointType") == "phone" and uri:
            return uri
    return None


def first_url(value: str | None) -> str | None:
    if not value:
        return None
    text = unescape(str(value))
    match = URL_RE.search(text)
    if not match:
        return None
    return clean_url(match.group(0))


def is_http_url(value: str | None) -> bool:
    return bool(value and value.lower().startswith(("http://", "https://")))


def clean_url(value: str) -> str:
    return unescape(value).strip().rstrip(TRAILING_URL_PUNCTUATION)


def safe_normalize_event(raw_event: dict[str, Any]) -> NormalizedEvent | None:
    """Normalize with logging instead of silent failure."""

    try:
        return normalize_event(raw_event)
    except Exception:
        logger.exception("Failed to normalize Google Calendar event id=%r", raw_event.get("id"))
        return None
