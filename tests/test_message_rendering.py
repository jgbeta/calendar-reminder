import _path  # noqa: F401
import unittest
from datetime import datetime, timezone

from calendar_slack_bot.calendar_events import normalize_event
from calendar_slack_bot.message_rendering import (
    render_slack_cancelled,
    render_slack_reminder,
    render_slack_rescheduled,
)


class MessageRenderingTests(unittest.TestCase):
    def test_render_does_not_require_hangout_link(self):
        raw = {
            "id": "zoom-1",
            "summary": "R&D <sync>",
            "start": {"dateTime": "2026-05-31T15:00:00Z"},
            "end": {"dateTime": "2026-05-31T15:30:00Z"},
            "creator": {"email": "host@example.com"},
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "video", "uri": "https://zoom.us/j/123"},
                ]
            },
        }
        event = normalize_event(raw)
        message = render_slack_reminder(event, 30)
        self.assertIn("Event starting in 30m.", message)
        self.assertIn("By: host@example.com", message)
        self.assertIn("https://zoom.us/j/123", message)
        self.assertIn("R&amp;D &lt;sync&gt;", message)
        self.assertNotIn("hangoutLink", message)

    def test_render_cancelled_shows_old_time(self):
        raw = {
            "id": "cancelled-1",
            "summary": "Weekly sync",
            "start": {"dateTime": "2026-05-31T15:00:00Z"},
            "end": {"dateTime": "2026-05-31T15:30:00Z"},
            "creator": {"email": "host@example.com"},
            "htmlLink": "https://calendar.google.com/event?eid=abc",
        }
        event = normalize_event(raw)
        message = render_slack_cancelled(event)
        self.assertIn("Event Deleted.", message)
        self.assertIn("Weekly sync", message)
        self.assertIn("By: host@example.com", message)
        self.assertIn("May 31, 2026", message)

    def test_render_rescheduled_pushed_back(self):
        raw = {
            "id": "reschedule-1",
            "summary": "1:1 with manager",
            "start": {"dateTime": "2026-05-31T16:00:00Z"},
            "end": {"dateTime": "2026-05-31T16:30:00Z"},
            "creator": {"email": "boss@example.com"},
            "conferenceData": {
                "entryPoints": [{"entryPointType": "video", "uri": "https://zoom.us/j/999"}]
            },
        }
        event = normalize_event(raw)
        old_start = datetime(2026, 5, 31, 15, 30, tzinfo=timezone.utc)
        message = render_slack_rescheduled(event, old_start)
        self.assertIn("Event updated, pushed back by 30 min.", message)
        self.assertIn("1:1 with manager", message)
        self.assertIn("By: boss@example.com", message)
        self.assertIn("https://zoom.us/j/999", message)

    def test_render_rescheduled_moved_up(self):
        raw = {
            "id": "reschedule-2",
            "summary": "Standup",
            "start": {"dateTime": "2026-05-31T14:00:00Z"},
            "end": {"dateTime": "2026-05-31T14:30:00Z"},
            "creator": {"email": "lead@example.com"},
            "hangoutLink": "https://meet.google.com/aaa-bbb-ccc",
        }
        event = normalize_event(raw)
        old_start = datetime(2026, 5, 31, 14, 30, tzinfo=timezone.utc)
        message = render_slack_rescheduled(event, old_start)
        self.assertIn("Event updated, moved up by 30 min.", message)
        self.assertIn("https://meet.google.com/aaa-bbb-ccc", message)


if __name__ == "__main__":
    unittest.main()
