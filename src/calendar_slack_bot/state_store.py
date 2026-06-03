from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3

from .calendar_events import NormalizedEvent, safe_normalize_event

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class DueReminder:
    event: NormalizedEvent
    offset_minutes: int
    scheduled_for: datetime


class SQLiteStateStore:
    """SQLite-backed cache for calendar events, reminders, and sync metadata."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                start TEXT,
                start_utc TEXT,
                end TEXT,
                end_utc TEXT,
                is_all_day INTEGER NOT NULL,
                join_url TEXT,
                location TEXT,
                calendar_url TEXT,
                updated TEXT,
                provider TEXT,
                dial_in_uri TEXT,
                creator_email TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                cancelled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS reminders (
                event_id TEXT NOT NULL,
                offset_minutes INTEGER NOT NULL,
                scheduled_for TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'sent', 'skipped', 'cancelled')),
                sent_at TEXT,
                skipped_at TEXT,
                cancelled_at TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (event_id, offset_minutes),
                FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_reminders_due
                ON reminders(status, scheduled_for);
            CREATE INDEX IF NOT EXISTS idx_events_start_utc
                ON events(start_utc);
            """
        )
        self.set_meta("schema_version", SCHEMA_VERSION)
        self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def get_sync_token(self) -> str | None:
        return self.get_meta("next_sync_token")

    def set_sync_token(self, sync_token: str) -> None:
        self.set_meta("next_sync_token", sync_token)
        self.conn.commit()

    def clear_sync_token(self) -> None:
        self.conn.execute("DELETE FROM meta WHERE key = ?", ("next_sync_token",))
        self.conn.commit()

    def write_heartbeat(self, when: datetime) -> None:
        self.set_meta("heartbeat_at", utc_iso(when))
        self.conn.commit()

    def read_heartbeat(self) -> datetime | None:
        value = self.get_meta("heartbeat_at")
        return parse_dt(value) if value else None

    def has_calendar_state(self) -> bool:
        row = self.conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM events) AS events_count,
                (SELECT COUNT(*) FROM reminders) AS reminders_count,
                (SELECT COUNT(*) FROM meta WHERE key = 'next_sync_token') AS token_count
            """
        ).fetchone()
        return bool(row["events_count"] or row["reminders_count"] or row["token_count"])

    def upsert_event(self, event: NormalizedEvent, seen_at: datetime) -> None:
        values = event_values(event, seen_at)
        self.conn.execute(
            """
            INSERT INTO events (
                id, status, title, start, start_utc, end, end_utc, is_all_day,
                join_url, location, calendar_url, updated, provider, dial_in_uri,
                creator_email, first_seen_at, last_seen_at, cancelled_at
            )
            VALUES (
                :id, :status, :title, :start, :start_utc, :end, :end_utc, :is_all_day,
                :join_url, :location, :calendar_url, :updated, :provider, :dial_in_uri,
                :creator_email, :first_seen_at, :last_seen_at, NULL
            )
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                title = excluded.title,
                start = excluded.start,
                start_utc = excluded.start_utc,
                end = excluded.end,
                end_utc = excluded.end_utc,
                is_all_day = excluded.is_all_day,
                join_url = excluded.join_url,
                location = excluded.location,
                calendar_url = excluded.calendar_url,
                updated = excluded.updated,
                provider = excluded.provider,
                dial_in_uri = excluded.dial_in_uri,
                creator_email = excluded.creator_email,
                last_seen_at = excluded.last_seen_at,
                cancelled_at = NULL
            """,
            values,
        )
        self.conn.commit()

    def mark_event_cancelled(self, event: NormalizedEvent, seen_at: datetime) -> None:
        existing = self.get_event(event.id)
        if existing is None:
            self.upsert_event(event, seen_at)
        self.conn.execute(
            """
            UPDATE events
            SET status = 'cancelled',
                updated = COALESCE(?, updated),
                last_seen_at = ?,
                cancelled_at = ?
            WHERE id = ?
            """,
            (event.updated, utc_iso(seen_at), utc_iso(seen_at), event.id),
        )
        self.conn.commit()

    def get_event(self, event_id: str) -> NormalizedEvent | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return event_from_row(row) if row else None

    def list_events(self) -> list[NormalizedEvent]:
        rows = self.conn.execute(
            """
            SELECT * FROM events
            ORDER BY start_utc IS NULL, start_utc, id
            """
        ).fetchall()
        return [event_from_row(row) for row in rows]

    def upsert_reminder(
        self,
        event_id: str,
        offset_minutes: int,
        scheduled_for: datetime,
        updated_at: datetime,
    ) -> None:
        scheduled_for_iso = utc_iso(scheduled_for)
        updated_at_iso = utc_iso(updated_at)
        row = self.conn.execute(
            """
            SELECT status FROM reminders
            WHERE event_id = ? AND offset_minutes = ?
            """,
            (event_id, offset_minutes),
        ).fetchone()
        if row and row["status"] == "sent":
            self.conn.execute(
                """
                UPDATE reminders
                SET scheduled_for = ?, updated_at = ?
                WHERE event_id = ? AND offset_minutes = ?
                """,
                (scheduled_for_iso, updated_at_iso, event_id, offset_minutes),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO reminders (
                    event_id, offset_minutes, scheduled_for, status,
                    sent_at, skipped_at, cancelled_at, last_error, updated_at
                )
                VALUES (?, ?, ?, 'pending', NULL, NULL, NULL, NULL, ?)
                ON CONFLICT(event_id, offset_minutes) DO UPDATE SET
                    scheduled_for = excluded.scheduled_for,
                    status = 'pending',
                    sent_at = NULL,
                    skipped_at = NULL,
                    cancelled_at = NULL,
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (event_id, offset_minutes, scheduled_for_iso, updated_at_iso),
            )
        self.conn.commit()

    def cancel_reminders(self, event_id: str, when: datetime, reason: str) -> None:
        self.conn.execute(
            """
            UPDATE reminders
            SET status = 'cancelled',
                cancelled_at = ?,
                last_error = ?,
                updated_at = ?
            WHERE event_id = ? AND status != 'sent'
            """,
            (utc_iso(when), reason, utc_iso(when), event_id),
        )
        self.conn.commit()

    def due_reminders(self, now: datetime, *, limit: int = 100) -> list[DueReminder]:
        rows = self.conn.execute(
            """
            SELECT
                reminders.offset_minutes,
                reminders.scheduled_for,
                events.*
            FROM reminders
            JOIN events ON events.id = reminders.event_id
            WHERE reminders.status = 'pending'
              AND reminders.scheduled_for <= ?
            ORDER BY reminders.scheduled_for, reminders.offset_minutes
            LIMIT ?
            """,
            (utc_iso(now), limit),
        ).fetchall()
        return [
            DueReminder(
                event=event_from_row(row),
                offset_minutes=int(row["offset_minutes"]),
                scheduled_for=parse_dt(row["scheduled_for"]),
            )
            for row in rows
        ]

    def mark_reminder_sent(self, event_id: str, offset_minutes: int, when: datetime) -> None:
        self.conn.execute(
            """
            UPDATE reminders
            SET status = 'sent',
                sent_at = ?,
                last_error = NULL,
                updated_at = ?
            WHERE event_id = ? AND offset_minutes = ?
            """,
            (utc_iso(when), utc_iso(when), event_id, offset_minutes),
        )
        self.conn.commit()

    def mark_reminder_error(self, event_id: str, offset_minutes: int, error: str, when: datetime) -> None:
        self.conn.execute(
            """
            UPDATE reminders
            SET last_error = ?,
                updated_at = ?
            WHERE event_id = ? AND offset_minutes = ? AND status = 'pending'
            """,
            (error[:500], utc_iso(when), event_id, offset_minutes),
        )
        self.conn.commit()

    def mark_reminder_skipped(self, event_id: str, offset_minutes: int, reason: str, when: datetime) -> None:
        self.conn.execute(
            """
            UPDATE reminders
            SET status = 'skipped',
                skipped_at = ?,
                last_error = ?,
                updated_at = ?
            WHERE event_id = ? AND offset_minutes = ? AND status = 'pending'
            """,
            (utc_iso(when), reason, utc_iso(when), event_id, offset_minutes),
        )
        self.conn.commit()

    def reminder_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT * FROM reminders
            ORDER BY event_id, offset_minutes
            """
        ).fetchall()

    def cleanup_old_state(self, cutoff: datetime) -> None:
        self.conn.execute(
            """
            DELETE FROM events
            WHERE COALESCE(start_utc, end_utc, cancelled_at, last_seen_at) < ?
            """,
            (utc_iso(cutoff),),
        )
        self.conn.commit()


def migrate_legacy_json_state(path: Path, store: SQLiteStateStore, seen_at: datetime) -> None:
    """Import the old JSON state file once when the SQLite cache is empty."""

    if store.has_calendar_state() or not path.exists():
        return

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read legacy sync state from %s", path)
        return

    sync_token = state.get("next_sync_token")
    if sync_token:
        store.set_sync_token(sync_token)

    imported = 0
    for raw_event in (state.get("events") or {}).values():
        if not isinstance(raw_event, dict):
            continue
        event = safe_normalize_event(raw_event)
        if event is None:
            continue
        if event.status == "cancelled":
            store.mark_event_cancelled(event, seen_at)
        else:
            store.upsert_event(event, seen_at)
        imported += 1

    if imported:
        logger.info("Imported %s events from legacy sync state %s", imported, path)


def event_values(event: NormalizedEvent, seen_at: datetime) -> dict[str, object]:
    return {
        "id": event.id,
        "status": event.status,
        "title": event.title,
        "start": dt_iso(event.start),
        "start_utc": utc_iso(event.start) if event.start else None,
        "end": dt_iso(event.end),
        "end_utc": utc_iso(event.end) if event.end else None,
        "is_all_day": 1 if event.is_all_day else 0,
        "join_url": event.join_url,
        "location": event.location,
        "calendar_url": event.calendar_url,
        "updated": event.updated,
        "provider": event.provider,
        "dial_in_uri": event.dial_in_uri,
        "creator_email": event.creator_email,
        "first_seen_at": utc_iso(seen_at),
        "last_seen_at": utc_iso(seen_at),
    }


def event_from_row(row: sqlite3.Row) -> NormalizedEvent:
    return NormalizedEvent(
        id=row["id"],
        status=row["status"],
        title=row["title"],
        start=parse_dt(row["start"]),
        end=parse_dt(row["end"]),
        is_all_day=bool(row["is_all_day"]),
        join_url=row["join_url"],
        location=row["location"],
        calendar_url=row["calendar_url"],
        updated=row["updated"],
        provider=row["provider"],
        dial_in_uri=row["dial_in_uri"],
        creator_email=row["creator_email"],
    )


def dt_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetime values stored in SQLite must be timezone-aware")
    return value.isoformat()


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime values stored in SQLite must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)
