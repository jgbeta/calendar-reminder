from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import signal
import sys
import time

from googleapiclient.discovery import build

from .auth import load_google_credentials
from .calendar_events import normalize_event
from .config import SCOPES, load_config_from_env
from .message_rendering import render_slack_reminder
from .slack_client import SlackMessenger
from .sync import CalendarSyncClient, SyncTokenExpired
from .timer_manager import InMemoryReminderManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    config = load_config_from_env()
    if not config.slack_bot_token:
        raise RuntimeError("Missing Slack token. Set SLACK_BOT_TOKEN or SLACK_BOT_TOKEN_FILE.")
    if not config.slack_dm_channel_id:
        raise RuntimeError("Missing SLACK_DM_CHANNEL_ID. Use a D... DM conversation id.")

    creds = load_google_credentials(
        SCOPES,
        credentials_path=config.google_credentials_path,
        token_path=config.google_token_path,
        headless=config.headless,
    )
    calendar_service = build("calendar", "v3", credentials=creds)
    sync_client = CalendarSyncClient(calendar_service)
    slack = SlackMessenger(config.slack_bot_token)
    timers = InMemoryReminderManager()
    state = load_state(config.google_sync_state_path)
    stopped = False

    def stop(signum, frame):  # noqa: ARG001
        nonlocal stopped
        stopped = True
        logger.info("Received signal %s; stopping", signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def send_reminder(event_id: str, offset: int) -> None:
        event_payload = state.get("events", {}).get(event_id)
        if not event_payload:
            logger.info("Skipping reminder for unknown event_id=%s", event_id)
            return
        event = normalize_event(event_payload)
        message = render_slack_reminder(event, offset)
        slack.post_message(config.slack_dm_channel_id, message)

    while not stopped:
        try:
            sync_token = state.get("next_sync_token")
            if sync_token:
                result = sync_client.incremental_sync(sync_token, calendar_id=config.calendar_id)
            else:
                result = sync_client.full_sync(calendar_id=config.calendar_id)

            state.setdefault("events", {})
            for raw_event in result.events:
                event_id = raw_event.get("id")
                if not event_id:
                    continue

                event = normalize_event(raw_event)
                if event.status == "cancelled":
                    timers.cancel_event(event.id)
                    state["events"].pop(event.id, None)
                    continue

                # Persist the raw event so the timer callback can re-render with
                # the latest normalized data.
                state["events"][event.id] = raw_event

                if event.is_all_day or event.start is None:
                    timers.cancel_event(event.id)
                    continue

                if event.start <= datetime.now(timezone.utc):
                    timers.cancel_event(event.id)
                    continue

                timers.schedule_event(event.id, event.start, (30, 5), send_reminder)

            state["next_sync_token"] = result.next_sync_token
            save_state(config.google_sync_state_path, state)

        except SyncTokenExpired:
            logger.warning("Sync token expired. Clearing state and running full sync on next loop.")
            state = {}
            save_state(config.google_sync_state_path, state)
        except Exception:
            logger.exception("Bot polling loop failed")

        for _ in range(config.poll_seconds):
            if stopped:
                break
            time.sleep(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
