from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import signal
import sys
import time

from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv(override=True)

from .auth import load_google_credentials
from .calendar_events import safe_normalize_event
from .config import SCOPES, load_config_from_env
from .message_rendering import render_slack_cancelled, render_slack_reminder, render_slack_rescheduled
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


def _post(slack: SlackMessenger, config, message: str) -> None:
    """Prepend the @mention if configured and post to Slack, logging failures."""
    try:
        if config.slack_mention_user_id:
            message = f"<@{config.slack_mention_user_id}>\n{message}"
        slack.post_message(config.slack_dm_channel_id, message)
        logger.info("Slack message sent: %s", message.splitlines()[0][:80])
    except Exception:
        logger.exception("Failed to post Slack message")


def main() -> int:
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    config = load_config_from_env()
    if not config.slack_bot_token:
        raise RuntimeError("Missing Slack token. Set SLACK_BOT_TOKEN or SLACK_BOT_TOKEN_FILE.")
    if not config.slack_dm_channel_id:
        raise RuntimeError("Missing SLACK_DM_CHANNEL_ID. Use a C... or D... conversation id.")

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
        event = safe_normalize_event(event_payload)
        if event is None:
            return
        _post(slack, config, render_slack_reminder(event, offset))

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

                event = safe_normalize_event(raw_event)
                if event is None:
                    continue

                if event.status == "cancelled":
                    old_raw = state.get("events", {}).get(event.id)
                    if old_raw:
                        old_event = safe_normalize_event(old_raw)
                        if old_event is not None:
                            _post(slack, config, render_slack_cancelled(old_event))
                    timers.cancel_event(event.id)
                    state["events"].pop(event.id, None)
                    continue

                if event.creator_email and event.creator_email in config.ignored_creator_emails:
                    logger.debug("Ignoring event from filtered creator %s", event.creator_email)
                    continue

                old_raw = state.get("events", {}).get(event.id)
                if old_raw:
                    old_event = safe_normalize_event(old_raw)
                    if old_event is not None and old_event.start != event.start:
                        _post(slack, config, render_slack_rescheduled(event, old_event.start))

                state["events"][event.id] = raw_event

                if event.is_all_day or event.start is None:
                    timers.cancel_event(event.id)
                    continue

                if event.start <= datetime.now(timezone.utc):
                    timers.cancel_event(event.id)
                    continue

                timers.schedule_event(event.id, event.start, (30, 5), send_reminder)
                logger.info("Scheduled reminders for %r at %s", event.title, event.start.isoformat())

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
