import _path  # noqa: F401
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from calendar_slack_bot.calendar_events import normalize_event
from calendar_slack_bot.config import BotConfig
from calendar_slack_bot.main import (
    SlackNotifier,
    handle_synced_event,
    process_due_reminders,
    reconcile_cached_events,
)
from calendar_slack_bot.state_store import SQLiteStateStore


class FakeSlackResponse(dict):
    def __init__(self, *args, headers=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers = headers or {}


class FakeRateLimitedError(Exception):
    def __init__(self, retry_after="1"):
        super().__init__("ratelimited")
        self.response = FakeSlackResponse({"error": "ratelimited"}, headers={"Retry-After": retry_after})


class FakeSlack:
    def __init__(self, fail=False, rate_limit_on=()):
        self.fail = fail
        self.rate_limit_on = set(rate_limit_on)
        self.messages = []
        self.calls = 0

    def post_message(self, channel, text):
        self.calls += 1
        if self.calls in self.rate_limit_on:
            raise FakeRateLimitedError()
        if self.fail:
            raise RuntimeError("Slack unavailable")
        self.messages.append((channel, text))
        return {"ok": True}


def make_config(*, ignored=(), cooldown_seconds=900, horizon_days=7):
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
        slack_rate_limit_warning_cooldown_seconds=cooldown_seconds,
        notification_horizon_days=horizon_days,
    )


def make_raw(event_id, summary, starts_at, *, creator="host@example.com"):
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": starts_at.isoformat()},
        "end": {"dateTime": (starts_at + timedelta(minutes=30)).isoformat()},
        "creator": {"email": creator},
    }


class MainReminderFlowTests(unittest.TestCase):
    def make_store(self, tmpdir):
        return SQLiteStateStore(Path(tmpdir) / "state.sqlite")

    def make_notifier(self, store, slack, config, now):
        return SlackNotifier(store, slack, config, now)

    def test_reconcile_sends_missed_reminder_before_event_start(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event(make_raw("event-1", "Soon", now + timedelta(minutes=10)))
        config = make_config()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)
                process_due_reminders(store, self.make_notifier(store, FakeSlack(), config, now), config, now)

                rows = {row["offset_minutes"]: row for row in store.reminder_rows()}
                self.assertEqual(rows[30]["status"], "sent")
                self.assertEqual(rows[5]["status"], "pending")
            finally:
                store.close()

    def test_slack_failure_leaves_due_reminder_pending_for_retry(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event(make_raw("event-1", "Soon", now + timedelta(minutes=10)))
        config = make_config()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)
                process_due_reminders(store, self.make_notifier(store, FakeSlack(fail=True), config, now), config, now)

                rows = {row["offset_minutes"]: row for row in store.reminder_rows()}
                self.assertEqual(rows[30]["status"], "pending")
                self.assertEqual(rows[30]["last_error"], "Slack post failed")
            finally:
                store.close()

    def test_rate_limited_reminder_is_suppressed_not_retried(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event(make_raw("event-1", "Soon", now + timedelta(minutes=10)))
        config = make_config(cooldown_seconds=900)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)
                process_due_reminders(store, self.make_notifier(store, FakeSlack(rate_limit_on=(1,)), config, now), config, now)

                rows = {row["offset_minutes"]: row for row in store.reminder_rows()}
                self.assertEqual(rows[30]["status"], "skipped")
                self.assertEqual(rows[30]["last_error"], "Slack rate limited; notification suppressed")
                self.assertEqual(store.get_slack_rate_limit_state().suppressed_count, 1)
            finally:
                store.close()

    def test_ignored_creator_does_not_create_or_send_reminders(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event(make_raw("event-1", "Ignored", now + timedelta(minutes=10), creator="HOST@example.com"))
        config = make_config(ignored=("host@example.com",))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)
                slack = FakeSlack()
                process_due_reminders(store, self.make_notifier(store, slack, config, now), config, now)

                self.assertEqual(store.reminder_rows(), [])
                self.assertEqual(slack.messages, [])
            finally:
                store.close()

    def test_event_outside_horizon_is_cached_without_reminder_rows(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event(make_raw("event-1", "Next month", now + timedelta(days=8)))
        config = make_config()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)

                self.assertEqual(store.get_event("event-1").title, "Next month")
                self.assertEqual(store.reminder_rows(), [])
            finally:
                store.close()

    def test_custom_horizon_includes_later_event(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event(make_raw("event-1", "Eight days out", now + timedelta(days=8)))
        config = make_config(horizon_days=8)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)

                rows = {row["offset_minutes"]: row for row in store.reminder_rows()}
                self.assertEqual(set(rows), {5, 30})
            finally:
                store.close()

    def test_event_entering_horizon_gets_reminder_rows(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        event = normalize_event(make_raw("event-1", "Soon enough", now + timedelta(days=8)))
        config = make_config()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)
                self.assertEqual(store.reminder_rows(), [])

                later = now + timedelta(days=1)
                reconcile_cached_events(store, config, later)
                rows = {row["offset_minutes"]: row for row in store.reminder_rows()}
                self.assertEqual(set(rows), {5, 30})
                self.assertTrue(all(row["status"] == "pending" for row in rows.values()))
            finally:
                store.close()

    def test_reschedule_updates_state_and_sends_one_notice(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        config = make_config()
        old_event = normalize_event(make_raw("event-1", "Planning", now + timedelta(hours=1)))
        new_raw = make_raw("event-1", "Planning", now + timedelta(hours=1, minutes=30))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(old_event, now)
                slack = FakeSlack()
                handle_synced_event(new_raw, store, self.make_notifier(store, slack, config, now), config, now)

                self.assertEqual(store.get_event("event-1").start.isoformat(), new_raw["start"]["dateTime"])
                self.assertEqual(len(slack.messages), 1)
                self.assertIn("pushed back by 30 min", slack.messages[0][1])
            finally:
                store.close()

    def test_reschedule_outside_horizon_is_suppressed(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        config = make_config()
        old_event = normalize_event(make_raw("event-1", "Planning", now + timedelta(days=8)))
        new_raw = make_raw("event-1", "Planning", now + timedelta(days=9))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(old_event, now)
                slack = FakeSlack()
                handle_synced_event(new_raw, store, self.make_notifier(store, slack, config, now), config, now)

                self.assertEqual(store.get_event("event-1").start.isoformat(), new_raw["start"]["dateTime"])
                self.assertEqual(slack.messages, [])
            finally:
                store.close()

    def test_cancelled_event_within_horizon_notifies_from_old_event(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        config = make_config()
        event = normalize_event(make_raw("event-1", "Planning", now + timedelta(hours=1)))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                reconcile_cached_events(store, config, now)
                slack = FakeSlack()
                handle_synced_event({"id": "event-1", "status": "cancelled"}, store, self.make_notifier(store, slack, config, now), config, now)

                self.assertEqual(store.get_event("event-1").status, "cancelled")
                self.assertTrue(all(row["status"] == "cancelled" for row in store.reminder_rows()))
                self.assertEqual(len(slack.messages), 1)
                self.assertIn("Event Deleted", slack.messages[0][1])
            finally:
                store.close()

    def test_cancelled_event_inside_custom_horizon_is_notified(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        config = make_config(horizon_days=8)
        event = normalize_event(make_raw("event-1", "Future planning", now + timedelta(days=8)))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                slack = FakeSlack()
                handle_synced_event({"id": "event-1", "status": "cancelled"}, store, self.make_notifier(store, slack, config, now), config, now)

                self.assertEqual(store.get_event("event-1").status, "cancelled")
                self.assertEqual(len(slack.messages), 1)
                self.assertIn("Event Deleted", slack.messages[0][1])
            finally:
                store.close()

    def test_cancelled_event_outside_horizon_is_not_notified(self):
        now = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
        config = make_config()
        event = normalize_event(make_raw("event-1", "Future planning", now + timedelta(days=8)))

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.upsert_event(event, now)
                slack = FakeSlack()
                handle_synced_event({"id": "event-1", "status": "cancelled"}, store, self.make_notifier(store, slack, config, now), config, now)

                self.assertEqual(store.get_event("event-1").status, "cancelled")
                self.assertEqual(slack.messages, [])
            finally:
                store.close()

    def test_bulk_recurring_cancellations_only_notify_within_horizon(self):
        now = datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)
        config = make_config()
        starts = [now + timedelta(days=1), now + timedelta(days=3), now + timedelta(days=8), now + timedelta(days=30)]

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                for index, starts_at in enumerate(starts):
                    store.upsert_event(normalize_event(make_raw(f"event-{index}", "Recurring", starts_at)), now)

                slack = FakeSlack()
                notifier = self.make_notifier(store, slack, config, now)
                for index in range(len(starts)):
                    handle_synced_event({"id": f"event-{index}", "status": "cancelled"}, store, notifier, config, now)

                self.assertEqual(len(slack.messages), 2)
                self.assertTrue(all("Event Deleted" in message for _, message in slack.messages))
                self.assertTrue(all(store.get_event(f"event-{index}").status == "cancelled" for index in range(len(starts))))
            finally:
                store.close()

    def test_rate_limit_stops_current_cancellation_burst(self):
        now = datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)
        config = make_config(cooldown_seconds=900)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                for index in range(3):
                    store.upsert_event(normalize_event(make_raw(f"event-{index}", "Recurring", now + timedelta(days=index + 1))), now)

                slack = FakeSlack(rate_limit_on=(1,))
                notifier = self.make_notifier(store, slack, config, now)
                for index in range(3):
                    handle_synced_event({"id": f"event-{index}", "status": "cancelled"}, store, notifier, config, now)

                self.assertEqual(slack.messages, [])
                self.assertEqual(slack.calls, 1)
                self.assertEqual(store.get_slack_rate_limit_state().suppressed_count, 3)
            finally:
                store.close()

    def test_rate_limit_warning_sends_after_cooldown_and_clears_state(self):
        now = datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)
        config = make_config(cooldown_seconds=900)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.record_slack_rate_limit(now - timedelta(seconds=901), 900, suppressed_count=3)
                slack = FakeSlack()
                result = self.make_notifier(store, slack, config, now).post("Normal notification")

                self.assertEqual(result.status, "sent")
                self.assertEqual(len(slack.messages), 2)
                self.assertIn("suppressed 3 notifications", slack.messages[0][1])
                self.assertEqual(slack.messages[1][1], "Normal notification")
                self.assertEqual(store.get_slack_rate_limit_state().suppressed_count, 0)
            finally:
                store.close()

    def test_rate_limited_warning_postpones_and_suppresses_normal_message(self):
        now = datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)
        config = make_config(cooldown_seconds=900)

        with tempfile.TemporaryDirectory() as tmpdir:
            store = self.make_store(tmpdir)
            try:
                store.record_slack_rate_limit(now - timedelta(seconds=901), 900, suppressed_count=3)
                slack = FakeSlack(rate_limit_on=(1,))
                result = self.make_notifier(store, slack, config, now).post("Normal notification")

                self.assertEqual(result.status, "suppressed")
                self.assertEqual(slack.messages, [])
                state = store.get_slack_rate_limit_state()
                self.assertEqual(state.suppressed_count, 4)
                self.assertGreater(state.warning_after, now)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
