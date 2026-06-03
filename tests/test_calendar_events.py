import _path  # noqa: F401
import unittest

from calendar_slack_bot.calendar_events import extract_join_url, normalize_event


class CalendarEventTests(unittest.TestCase):
    def test_google_meet_hangout_link(self):
        raw = {
            "id": "meet-1",
            "summary": "Meet call",
            "start": {"dateTime": "2026-05-31T15:00:00-05:00"},
            "end": {"dateTime": "2026-05-31T15:30:00-05:00"},
            "hangoutLink": "https://meet.google.com/aaa-bbbb-ccc",
        }
        event = normalize_event(raw)
        self.assertEqual(event.join_url, "https://meet.google.com/aaa-bbbb-ccc")

    def test_zoom_conference_data_video_entrypoint(self):
        raw = {
            "id": "zoom-1",
            "summary": "Zoom call",
            "start": {"dateTime": "2026-05-31T15:00:00-05:00"},
            "end": {"dateTime": "2026-05-31T15:30:00-05:00"},
            "conferenceData": {
                "conferenceSolution": {"name": "Zoom", "key": {"type": "addOn"}},
                "entryPoints": [
                    {"entryPointType": "phone", "uri": "tel:+123456789"},
                    {"entryPointType": "video", "uri": "https://zoom.us/j/123456789?pwd=abc"},
                ],
            },
        }
        event = normalize_event(raw)
        self.assertEqual(event.provider, "Zoom")
        self.assertEqual(event.join_url, "https://zoom.us/j/123456789?pwd=abc")
        self.assertEqual(event.dial_in_uri, "tel:+123456789")

    def test_description_url_with_apostrophe_in_title(self):
        raw = {
            "id": "desc-1",
            "summary": "Sam's planning meeting",
            "start": {"dateTime": "2026-05-31T15:00:00Z"},
            "end": {"dateTime": "2026-05-31T15:30:00Z"},
            "description": "Join here: <a href='https://teams.microsoft.com/l/meetup-join/abc'>link</a>",
        }
        event = normalize_event(raw)
        self.assertEqual(event.title, "Sam's planning meeting")
        self.assertEqual(event.join_url, "https://teams.microsoft.com/l/meetup-join/abc")

    def test_cancelled_event_only_requires_id(self):
        raw = {"id": "deleted-1", "status": "cancelled"}
        event = normalize_event(raw)
        self.assertEqual(event.id, "deleted-1")
        self.assertEqual(event.status, "cancelled")
        self.assertIsNone(event.start)

    def test_extract_join_url_falls_back_to_location(self):
        raw = {"location": "https://zoom.us/j/987654321)."}
        self.assertEqual(extract_join_url(raw), "https://zoom.us/j/987654321")

    def test_creator_email_is_extracted(self):
        raw = {
            "id": "creator-1",
            "summary": "Team sync",
            "start": {"dateTime": "2026-05-31T15:00:00Z"},
            "end": {"dateTime": "2026-05-31T15:30:00Z"},
            "creator": {"email": "organizer@example.com"},
            "hangoutLink": "https://meet.google.com/xxx-yyyy-zzz",
        }
        event = normalize_event(raw)
        self.assertEqual(event.creator_email, "organizer@example.com")

    def test_creator_email_is_none_when_absent(self):
        raw = {
            "id": "no-creator-1",
            "summary": "No creator",
            "start": {"dateTime": "2026-05-31T15:00:00Z"},
            "end": {"dateTime": "2026-05-31T15:30:00Z"},
            "hangoutLink": "https://meet.google.com/aaa-bbb-ccc",
        }
        event = normalize_event(raw)
        self.assertIsNone(event.creator_email)


if __name__ == "__main__":
    unittest.main()
