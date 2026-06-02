from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from threading import RLock, Timer
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


class Cancellable(Protocol):
    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], Cancellable]
ReminderCallback = Callable[[str, int], None]


@dataclass
class ReminderSlot:
    timer: Cancellable | None = None
    run_at: datetime | None = None


@dataclass
class EventTimers:
    reminders: dict[int, ReminderSlot] = field(default_factory=dict)


class InMemoryReminderManager:
    """Thread-safe, idempotent replacement for raw dict + threading.Timer usage."""

    def __init__(self, timer_factory: TimerFactory | None = None, now: Callable[[], datetime] | None = None):
        self._lock = RLock()
        self._events: dict[str, EventTimers] = {}
        self._timer_factory = timer_factory or self._default_timer_factory
        self._now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _default_timer_factory(delay_seconds: float, callback: Callable[[], None]) -> Cancellable:
        timer = Timer(delay_seconds, callback)
        timer.daemon = True
        timer.start()
        return timer

    def schedule_event(
        self,
        event_id: str,
        starts_at: datetime,
        offsets_minutes: tuple[int, ...],
        callback: ReminderCallback,
    ) -> None:
        """Schedule reminders for an event.

        Existing timers for the same event/offset are cancelled and replaced.
        Reminders that would fire in the past are skipped and left as None.
        """

        if starts_at.tzinfo is None:
            raise ValueError("starts_at must be timezone-aware")

        with self._lock:
            event_timers = self._events.setdefault(event_id, EventTimers())
            for offset in offsets_minutes:
                self._cancel_slot_locked(event_id, offset)

                run_at = starts_at - timedelta(minutes=offset)
                delay = (run_at - self._now()).total_seconds()
                slot = event_timers.reminders.setdefault(offset, ReminderSlot())
                slot.run_at = run_at

                if delay <= 0:
                    logger.info("Skipping past reminder event_id=%s offset=%s run_at=%s", event_id, offset, run_at)
                    slot.timer = None
                    continue

                def fire(event_id: str = event_id, offset: int = offset) -> None:
                    try:
                        callback(event_id, offset)
                    except Exception:
                        logger.exception("Reminder callback failed event_id=%s offset=%s", event_id, offset)
                    finally:
                        # Preserve the slot but mark the timer inactive.
                        self.cancel_timer(event_id, offset)

                slot.timer = self._timer_factory(delay, fire)

    def cancel_timer(self, event_id: str, offset_minutes: int) -> None:
        """Cancel one reminder. Safe if event/timer does not exist."""

        with self._lock:
            self._cancel_slot_locked(event_id, offset_minutes)

    def cancel_event(self, event_id: str) -> None:
        """Cancel all reminders for one event. Safe if the event does not exist."""

        with self._lock:
            event_timers = self._events.get(event_id)
            if not event_timers:
                return
            for offset in list(event_timers.reminders):
                self._cancel_slot_locked(event_id, offset)
            self._events.pop(event_id, None)

    def _cancel_slot_locked(self, event_id: str, offset_minutes: int) -> None:
        event_timers = self._events.get(event_id)
        if not event_timers:
            return
        slot = event_timers.reminders.setdefault(offset_minutes, ReminderSlot())
        timer = slot.timer
        if timer is not None:
            try:
                timer.cancel()
            finally:
                slot.timer = None
        else:
            slot.timer = None

    def snapshot(self) -> dict[str, dict[int, dict[str, str | bool | None]]]:
        """Return a JSON-ish view useful for debugging/tests."""

        with self._lock:
            result: dict[str, dict[int, dict[str, str | bool | None]]] = {}
            for event_id, event_timers in self._events.items():
                result[event_id] = {}
                for offset, slot in event_timers.reminders.items():
                    result[event_id][offset] = {
                        "has_timer": slot.timer is not None,
                        "run_at": slot.run_at.isoformat() if slot.run_at else None,
                    }
            return result
