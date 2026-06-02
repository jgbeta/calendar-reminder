from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Callable

from .calendar_events import NormalizedEvent
from .message_rendering import render_slack_reminder


@dataclass
class ScheduledReminderRecord:
    scheduled_message_id: str
    post_at: int
    channel: str


@dataclass
class EventScheduleRecord:
    event_updated: str | None
    reminders: dict[int, ScheduledReminderRecord] = field(default_factory=dict)


class JsonScheduledMessageStore:
    """Tiny JSON store for Slack scheduled message IDs."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, EventScheduleRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.data = {}
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = {
            event_id: EventScheduleRecord(
                event_updated=record.get("event_updated"),
                reminders={
                    int(offset): ScheduledReminderRecord(**value)
                    for offset, value in record.get("reminders", {}).items()
                },
            )
            for event_id, record in raw.items()
        }

    def save(self) -> None:
        raw = {}
        for event_id, record in self.data.items():
            raw[event_id] = {
                "event_updated": record.event_updated,
                "reminders": {
                    str(offset): vars(reminder)
                    for offset, reminder in record.reminders.items()
                },
            }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class SlackScheduledReminderService:
    """Schedule/cancel reminders using Slack's scheduled message API."""

    def __init__(self, web_client, store: JsonScheduledMessageStore, now: Callable[[], datetime]):
        self.client = web_client
        self.store = store
        self.now = now

    def reconcile_event(self, event: NormalizedEvent, channel: str, offsets_minutes: tuple[int, ...] = (30, 5)) -> None:
        """Delete old scheduled messages and create new ones for the event."""

        self.cancel_event(event.id)

        if event.status == "cancelled" or event.start is None or event.is_all_day:
            return

        record = EventScheduleRecord(event_updated=event.updated)
        for offset in offsets_minutes:
            post_at_dt = event.start - timedelta(minutes=offset)
            if post_at_dt <= self.now():
                continue
            message = render_slack_reminder(event, offset)
            response = self.client.chat_scheduleMessage(
                channel=channel,
                text=message,
                post_at=int(post_at_dt.timestamp()),
            )
            data = dict(response)
            if not data.get("ok", False):
                raise RuntimeError(f"Slack chat.scheduleMessage returned ok=false: {data.get('error')}")
            record.reminders[offset] = ScheduledReminderRecord(
                scheduled_message_id=data["scheduled_message_id"],
                post_at=int(post_at_dt.timestamp()),
                channel=channel,
            )

        self.store.data[event.id] = record
        self.store.save()

    def cancel_event(self, event_id: str) -> None:
        record = self.store.data.get(event_id)
        if not record:
            return
        for reminder in list(record.reminders.values()):
            response = self.client.chat_deleteScheduledMessage(
                channel=reminder.channel,
                scheduled_message_id=reminder.scheduled_message_id,
            )
            data = dict(response)
            if not data.get("ok", False) and data.get("error") not in {"invalid_scheduled_message_id", "message_not_found"}:
                raise RuntimeError(f"Slack chat.deleteScheduledMessage returned ok=false: {data.get('error')}")
        self.store.data.pop(event_id, None)
        self.store.save()
