from __future__ import annotations

import math
from datetime import datetime, timedelta

from .calendar_events import NormalizedEvent

__all__ = [
    "render_slack_reminder",
    "render_slack_cancelled",
    "render_slack_rescheduled",
]


def slack_escape(value: str | None) -> str:
    """Escape &, <, > which Slack reserves in message text."""
    if value is None:
        return ""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_start(value: datetime | None) -> str:
    """Format a datetime as 'Monday, June 01, 2026 at 03:00 PM'."""
    if value is None:
        return "unknown time"
    return value.strftime("%A, %B %d, %Y at %I:%M %p")


def format_time_delta(seconds: float) -> str:
    """Format a duration in seconds as '30 min' or '1:30 h'."""
    seconds = abs(seconds)
    if seconds < 3600:
        return f"{math.ceil(seconds / 60)} min"
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    return f"{h}:{str(m).zfill(2)} h"


def render_slack_reminder(event: NormalizedEvent, minutes_before: int) -> str:
    """Build a reminder message matching the original json_parse format."""

    if event.status == "cancelled":
        raise ValueError(f"Refusing to render reminder for cancelled event {event.id!r}")

    link = event.join_url or event.location or "(no meeting link)"
    return (
        f"Event starting in {minutes_before}m.\n"
        f"{slack_escape(event.title)}\n"
        f"By: {slack_escape(event.creator_email or 'unknown')}\n"
        f"{format_start(event.start)}\n"
        f"{link}"
    )


def render_slack_cancelled(event: NormalizedEvent) -> str:
    """Build a cancellation notice matching the original sendNotification format."""

    return (
        f"Event Deleted.\n"
        f"{slack_escape(event.title)}\n"
        f"By: {slack_escape(event.creator_email or 'unknown')}\n"
        f"{format_start(event.start)}"
    )


def render_slack_rescheduled(event: NormalizedEvent, old_start: datetime) -> str:
    """Build a reschedule notice with 'pushed back by X' / 'moved up by X' wording."""

    delta = (event.start - old_start).total_seconds()
    if delta > 0:
        direction = f"Event updated, pushed back by {format_time_delta(delta)}."
    else:
        direction = f"Event updated, moved up by {format_time_delta(delta)}."

    link = event.join_url or event.location or "(no meeting link)"
    return (
        f"{direction}\n"
        f"{slack_escape(event.title)}\n"
        f"By: {slack_escape(event.creator_email or 'unknown')}\n"
        f"{format_start(event.start)}\n"
        f"{link}"
    )
