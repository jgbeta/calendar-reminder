import _path  # noqa: F401
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from calendar_slack_bot.calendar_events import normalize_event
from calendar_slack_bot.state_store import SQLiteStateStore, migrate_legacy_json_state


class SQLiteStateStoreTests(unittest.TestCase):
    def make_store(self, tmpdir):
        return SQLiteStateStore(Path(tmpdir) / "state.sqlite")

    def test_upsert_event_preserves_local_start_and_due_reminder(self):
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
                store.upsert_reminder(event.id, 30, event.start - timedelta(minutes=30), now)

                saved = store.get_event(event.id)
                self.assertEqual(saved.start.isoformat(), "2026-06-02T10:10:00+00:00")
                due = store.due_reminders(now)
                self.assertEqual(len(due), 1)
                self.assertEqual(due[0].event.id, "event-1")
                self.assertEqual(due[0].offset_minutes, 30)
            finally:
                store.close()

    def test_sent_reminder_is_not_reopened_by_reconcile(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event({
            "id": "event-1",
            "summary": "Later",
            "start": {"dateTime": "2026-06-02T11:00:00+00:00"},
            "end": {"dateTime": "2026-06-02T11:30:00+00:00"},
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                store.upsert_reminder(event.id, 30, event.start - timedelta(minutes=30), now)
                store.mark_reminder_sent(event.id, 30, now)
                store.upsert_reminder(event.id, 30, event.start + timedelta(minutes=30), now)

                rows = store.reminder_rows()
                self.assertEqual(rows[0]["status"], "sent")
            finally:
                store.close()

    def test_migrates_legacy_json_state_once(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        legacy = {
            "next_sync_token": "sync-1",
            "events": {
                "event-1": {
                    "id": "event-1",
                    "summary": "Migrated",
                    "start": {"dateTime": "2026-06-02T11:00:00+00:00"},
                    "end": {"dateTime": "2026-06-02T11:30:00+00:00"},
                }
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_path = Path(tmpdir) / "google-sync-state.json"
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
            store = self.make_store(tmpdir)
            try:
                migrate_legacy_json_state(legacy_path, store, now)
                self.assertEqual(store.get_sync_token(), "sync-1")
                self.assertEqual(store.get_event("event-1").title, "Migrated")

                legacy["next_sync_token"] = "sync-2"
                legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
                migrate_legacy_json_state(legacy_path, store, now)
                self.assertEqual(store.get_sync_token(), "sync-1")
            finally:
                store.close()

    def test_cleanup_removes_events_older_than_a_week(self):
        now = datetime(2026, 6, 10, 10, 0, tzinfo=timezone.utc)
        old_event = normalize_event({
            "id": "old",
            "summary": "Old",
            "start": {"dateTime": "2026-06-01T10:00:00+00:00"},
            "end": {"dateTime": "2026-06-01T10:30:00+00:00"},
        })
        new_event = normalize_event({
            "id": "new",
            "summary": "New",
            "start": {"dateTime": "2026-06-09T10:00:00+00:00"},
            "end": {"dateTime": "2026-06-09T10:30:00+00:00"},
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(old_event, now)
                store.upsert_event(new_event, now)
                store.cleanup_old_state(now - timedelta(days=7))

                self.assertIsNone(store.get_event("old"))
                self.assertIsNotNone(store.get_event("new"))
            finally:
                store.close()


    def test_clearing_sync_token_preserves_cached_events_and_reminders(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event({
            "id": "event-1",
            "summary": "Cached",
            "start": {"dateTime": "2026-06-02T11:00:00+00:00"},
            "end": {"dateTime": "2026-06-02T11:30:00+00:00"},
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.set_sync_token("sync-1")
                store.upsert_event(event, now)
                store.upsert_reminder(event.id, 30, event.start - timedelta(minutes=30), now)
                store.clear_sync_token()

                self.assertIsNone(store.get_sync_token())
                self.assertEqual(store.get_event("event-1").title, "Cached")
                self.assertEqual(store.reminder_rows()[0]["status"], "pending")
            finally:
                store.close()

    def test_heartbeat_round_trips(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.write_heartbeat(now)
                self.assertEqual(store.read_heartbeat(), now)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
