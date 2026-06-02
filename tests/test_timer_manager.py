import unittest
from datetime import datetime, timedelta, timezone

from calendar_slack_bot.timer_manager import InMemoryReminderManager


class FakeTimer:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class TimerManagerTests(unittest.TestCase):
    def test_cancel_missing_timer_is_safe(self):
        manager = InMemoryReminderManager(timer_factory=lambda delay, cb: FakeTimer())
        manager.cancel_timer("missing", 30)
        manager.cancel_event("missing")

    def test_schedule_then_cancel_preserves_slot_with_none_timer(self):
        fake_timers = []

        def factory(delay, callback):
            timer = FakeTimer()
            fake_timers.append(timer)
            return timer

        now = datetime(2026, 5, 31, 10, 0, tzinfo=timezone.utc)
        manager = InMemoryReminderManager(timer_factory=factory, now=lambda: now)
        manager.schedule_event("event-1", now + timedelta(hours=1), (30,), lambda event_id, offset: None)
        self.assertTrue(manager.snapshot()["event-1"][30]["has_timer"])

        manager.cancel_timer("event-1", 30)
        snapshot = manager.snapshot()
        self.assertFalse(snapshot["event-1"][30]["has_timer"])
        self.assertTrue(fake_timers[0].cancelled)

        # Re-cancel should not raise.
        manager.cancel_timer("event-1", 30)

    def test_past_reminder_is_skipped_but_slot_exists(self):
        now = datetime(2026, 5, 31, 10, 0, tzinfo=timezone.utc)
        manager = InMemoryReminderManager(timer_factory=lambda delay, cb: FakeTimer(), now=lambda: now)
        manager.schedule_event("event-1", now + timedelta(minutes=3), (5,), lambda event_id, offset: None)
        snapshot = manager.snapshot()
        self.assertFalse(snapshot["event-1"][5]["has_timer"])


if __name__ == "__main__":
    unittest.main()
