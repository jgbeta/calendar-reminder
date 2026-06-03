import _path  # noqa: F401
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from calendar_slack_bot.calendar_events import normalize_event
from calendar_slack_bot.config import BotConfig
from calendar_slack_bot.main import (
    handle_synced_event,
    process_due_reminders,
    reconcile_cached_events,
)
from calendar_slack_bot.state_store import SQLiteStateStore


class FakeSlack:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    def post_message(self, channel, text):
        if self.fail:
            raise RuntimeError("Slack unavailable")
        self.messages.append((channel, text))
        return {"ok": True}


def make_config(*, ignored=()):
    return BotConfig(
        calendar_id="primary",
        poll_seconds=60,
        google_credentials_path=Path("credentials.json"),
        google_token_path=Path("token.json"),
        google_sync_state_path=Path("google-sync-state.json"),
        calendar_bot_db_path=Path("calendar-bot.sqlite"),
        slack_bot_token="xoxb-test",
        slack_dm_channel_id="C123",
        headless=True,
        ignored_creator_emails=frozenset(email.lower() for email in ignored),
        slack_mention_user_id=None,
    )


class MainReminderFlowTests(unittest.TestCase):
    def make_store(self, tmpdir):
        return SQLiteStateStore(Path(tmpdir) / "state.sqlite")

    def test_reconcile_sends_missed_reminder_before_event_start(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event({
            "id": "event-1",
            "summary": "Soon",
            "start": {"dateTime": "2026-06-02T10:10:00+00:00"},
            "end": {"dateTime": "2026-06-02T10:40:00+00:00"},
            "creator": {"email": "host@example.com"},
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, make_config(), now)
                process_due_reminders(store, FakeSlack(), make_config(), now)

                rows = {row["offset_minutes"]: row for row in store.reminder_rows()}
                self.assertEqual(rows[30]["status"], "sent")
                self.assertEqual(rows[5]["status"], "pending")
            finally:
                store.close()

    def test_slack_failure_leaves_due_reminder_pending_for_retry(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event({
            "id": "event-1",
            "summary": "Soon",
            "start": {"dateTime": "2026-06-02T10:10:00+00:00"},
            "end": {"dateTime": "2026-06-02T10:40:00+00:00"},
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, make_config(), now)
                process_due_reminders(store, FakeSlack(fail=True), make_config(), now)

                rows = {row["offset_minutes"]: row for row in store.reminder_rows()}
                self.assertEqual(rows[30]["status"], "pending")
                self.assertEqual(rows[30]["last_error"], "Slack post failed")
            finally:
                store.close()

    def test_ignored_creator_does_not_create_or_send_reminders(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event({
            "id": "event-1",
            "summary": "Ignored",
            "start": {"dateTime": "2026-06-02T10:10:00+00:00"},
            "end": {"dateTime": "2026-06-02T10:40:00+00:00"},
            "creator": {"email": "HOST@example.com"},
        })
        config = make_config(ignored=("host@example.com",))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)
                slack = FakeSlack()
                process_due_reminders(store, slack, config, now)

                self.assertEqual(store.reminder_rows(), [])
                self.assertEqual(slack.messages, [])
            finally:
                store.close()

    def test_reschedule_updates_state_and_sends_one_notice(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        config = make_config()
        old_event = normalize_event({
            "id": "event-1",
            "summary": "Planning",
            "start": {"dateTime": "2026-06-02T11:00:00+00:00"},
            "end": {"dateTime": "2026-06-02T11:30:00+00:00"},
            "creator": {"email": "host@example.com"},
        })
        new_raw = {
            "id": "event-1",
            "summary": "Planning",
            "start": {"dateTime": "2026-06-02T11:30:00+00:00"},
            "end": {"dateTime": "2026-06-02T12:00:00+00:00"},
            "creator": {"email": "host@example.com"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(old_event, now)
                slack = FakeSlack()
                handle_synced_event(new_raw, store, slack, config, now)

                self.assertEqual(store.get_event("event-1").start.isoformat(), "2026-06-02T11:30:00+00:00")
                self.assertEqual(len(slack.messages), 1)
                self.assertIn("pushed back by 30 min", slack.messages[0][1])
            finally:
                store.close()

    def test_cancelled_event_cancels_pending_reminders_and_notifies_from_old_event(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        config = make_config()
        event = normalize_event({
            "id": "event-1",
            "summary": "Planning",
            "start": {"dateTime": "2026-06-02T11:00:00+00:00"},
            "end": {"dateTime": "2026-06-02T11:30:00+00:00"},
            "creator": {"email": "host@example.com"},
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)
                slack = FakeSlack()
                handle_synced_event({"id": "event-1", "status": "cancelled"}, store, slack, config, now)

                self.assertEqual(store.get_event("event-1").status, "cancelled")
                self.assertTrue(all(row["status"] == "cancelled" for row in store.reminder_rows()))
                self.assertEqual(len(slack.messages), 1)
                self.assertIn("Event Deleted", slack.messages[0][1])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
