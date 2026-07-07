from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import signal
import sys
import time

from .auth import load_google_credentials
from .calendar_events import NormalizedEvent, safe_normalize_event
from .config import BotConfig, SCOPES, load_config_from_env
from .message_rendering import render_slack_cancelled, render_slack_reminder, render_slack_rescheduled
from .slack_client import SlackMessenger
from .state_store import SQLiteStateStore, migrate_legacy_json_state
from .sync import CalendarSyncClient, SyncTokenExpired

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

REMINDER_OFFSETS_MINUTES = (30, 5)
HISTORY_RETENTION_DAYS = 7
POST_SENT = "sent"
POST_FAILED = "failed"
POST_RATE_LIMITED = "rate_limited"
POST_SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class SlackPostResult:
    status: str
    retry_after_seconds: int | None = None


class SlackNotifier:
    """Posts Slack messages with durable rate-limit suppression."""

    def __init__(self, store: SQLiteStateStore, slack: SlackMessenger, config: BotConfig, now: datetime):
        self.store = store
        self.slack = slack
        self.config = config
        self.now = now
        self.blocked = False
        self.warning_checked = False

    def post(self, message: str) -> SlackPostResult:
        if self.blocked:
            self.store.record_suppressed_slack_notification(self.now)
            return SlackPostResult(POST_SUPPRESSED)

        if not self.warning_checked:
            self.warning_checked = True
            if not self._maybe_send_rate_limit_warning():
                self.store.record_suppressed_slack_notification(self.now)
                return SlackPostResult(POST_SUPPRESSED)

        result = _post(self.slack, self.config, message)
        if result.status == POST_RATE_LIMITED:
            self.blocked = True
            self.store.record_slack_rate_limit(
                self.now,
                self.config.slack_rate_limit_warning_cooldown_seconds,
                result.retry_after_seconds,
                suppressed_count=1,
            )
        return result

    def _maybe_send_rate_limit_warning(self) -> bool:
        state = self.store.get_slack_rate_limit_state()
        if state.suppressed_count <= 0:
            return True
        if state.warning_after is not None and self.now < state.warning_after:
            self.blocked = True
            return False

        message = (
            "Calendar bot hit Slack rate limits and suppressed "
            f"{state.suppressed_count} notifications. Future notifications are now resuming."
        )
        result = _post(self.slack, self.config, message)
        if result.status == POST_SENT:
            self.store.clear_slack_rate_limit_state()
            return True

        self.blocked = True
        if result.status == POST_RATE_LIMITED:
            self.store.record_slack_rate_limit(
                self.now,
                self.config.slack_rate_limit_warning_cooldown_seconds,
                result.retry_after_seconds,
                suppressed_count=0,
            )
        else:
            self.store.postpone_slack_rate_limit_warning(
                self.now,
                self.config.slack_rate_limit_warning_cooldown_seconds,
            )
        return False


def load_dotenv_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(override=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _post(slack: SlackMessenger, config: BotConfig, message: str) -> SlackPostResult:
    """Prepend the @mention if configured and post to Slack, logging failures."""
    try:
        if config.slack_mention_user_id:
            message = f"<@{config.slack_mention_user_id}>\n{message}"
        slack.post_message(config.slack_dm_channel_id, message)
        logger.info("Slack message sent: %s", message.splitlines()[0][:80])
        return SlackPostResult(POST_SENT)
    except Exception as exc:
        if is_slack_rate_limited(exc):
            logger.warning("Slack rate-limited calendar bot notifications; suppressing the current burst")
            return SlackPostResult(POST_RATE_LIMITED, retry_after_seconds=slack_retry_after_seconds(exc))
        logger.exception("Failed to post Slack message")
        return SlackPostResult(POST_FAILED)


def is_slack_rate_limited(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if response is None:
        return False
    try:
        return response.get("error") == "ratelimited"
    except Exception:
        return False


def slack_retry_after_seconds(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    value = None
    for key in ("Retry-After", "retry-after"):
        if key in headers:
            value = headers[key]
            break
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def notification_horizon_end(now: datetime, config: BotConfig) -> datetime:
    return now + timedelta(days=config.notification_horizon_days)


def is_ignored_creator(event: NormalizedEvent, config: BotConfig) -> bool:
    if not event.creator_email:
        return False
    return event.creator_email.strip().lower() in config.ignored_creator_emails


def is_future_actionable_event(event: NormalizedEvent, config: BotConfig, now: datetime) -> bool:
    return (
        event.status != "cancelled"
        and not event.is_all_day
        and event.start is not None
        and event.start > now
        and not is_ignored_creator(event, config)
    )


def is_within_notification_horizon(event: NormalizedEvent, config: BotConfig, now: datetime) -> bool:
    return event.start is not None and now < event.start <= notification_horizon_end(now, config)


def is_event_eligible_for_reminders(event: NormalizedEvent, config: BotConfig, now: datetime) -> bool:
    return is_future_actionable_event(event, config, now) and is_within_notification_horizon(event, config, now)


def is_event_eligible_for_change_notification(event: NormalizedEvent, config: BotConfig, now: datetime) -> bool:
    return is_future_actionable_event(event, config, now) and is_within_notification_horizon(event, config, now)


def is_reschedule_notifiable(old_event: NormalizedEvent, event: NormalizedEvent, config: BotConfig, now: datetime) -> bool:
    if not is_future_actionable_event(old_event, config, now):
        return False
    if not is_future_actionable_event(event, config, now):
        return False
    return is_within_notification_horizon(old_event, config, now) or is_within_notification_horizon(event, config, now)


def reconcile_event_reminders(
    store: SQLiteStateStore,
    event: NormalizedEvent,
    config: BotConfig,
    now: datetime,
    offsets_minutes: tuple[int, ...] = REMINDER_OFFSETS_MINUTES,
) -> None:
    if not is_event_eligible_for_reminders(event, config, now):
        store.cancel_reminders(event.id, now, "event is not eligible for reminders")
        return

    for offset in offsets_minutes:
        scheduled_for = event.start - timedelta(minutes=offset)
        store.upsert_reminder(event.id, offset, scheduled_for, now)


def reconcile_cached_events(store: SQLiteStateStore, config: BotConfig, now: datetime) -> None:
    for event in store.list_events():
        reconcile_event_reminders(store, event, config, now)


def handle_synced_event(
    raw_event: dict,
    store: SQLiteStateStore,
    notifier: SlackNotifier,
    config: BotConfig,
    now: datetime,
) -> None:
    event = safe_normalize_event(raw_event)
    if event is None:
        return

    old_event = store.get_event(event.id)

    if event.status == "cancelled":
        store.mark_event_cancelled(event, now)
        store.cancel_reminders(event.id, now, "event cancelled")
        if old_event is not None and is_event_eligible_for_change_notification(old_event, config, now):
            notifier.post(render_slack_cancelled(old_event))
        return

    store.upsert_event(event, now)
    reconcile_event_reminders(store, event, config, now)

    if old_event is not None and old_event.start != event.start and is_reschedule_notifiable(old_event, event, config, now):
        notifier.post(render_slack_rescheduled(event, old_event.start))


def sync_calendar_once(
    store: SQLiteStateStore,
    sync_client: CalendarSyncClient,
    notifier: SlackNotifier,
    config: BotConfig,
    now: datetime,
) -> None:
    sync_token = store.get_sync_token()
    if sync_token:
        result = sync_client.incremental_sync(sync_token, calendar_id=config.calendar_id)
    else:
        result = sync_client.full_sync(calendar_id=config.calendar_id)

    for raw_event in result.events:
        event_id = raw_event.get("id")
        if not event_id:
            continue
        handle_synced_event(raw_event, store, notifier, config, now)

    store.set_sync_token(result.next_sync_token)


def process_due_reminders(store: SQLiteStateStore, notifier: SlackNotifier, config: BotConfig, now: datetime) -> None:
    for reminder in store.due_reminders(now):
        event = reminder.event
        if not is_event_eligible_for_reminders(event, config, now):
            store.mark_reminder_skipped(event.id, reminder.offset_minutes, "event is no longer eligible", now)
            continue

        message = render_slack_reminder(event, reminder.offset_minutes)
        result = notifier.post(message)
        if result.status == POST_SENT:
            store.mark_reminder_sent(event.id, reminder.offset_minutes, now)
        elif result.status in {POST_RATE_LIMITED, POST_SUPPRESSED}:
            store.mark_reminder_skipped(event.id, reminder.offset_minutes, "Slack rate limited; notification suppressed", now)
        else:
            store.mark_reminder_error(event.id, reminder.offset_minutes, "Slack post failed", now)


def main() -> int:
    load_dotenv_file()
    from googleapiclient.discovery import build

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
    store = SQLiteStateStore(config.calendar_bot_db_path)
    migrate_legacy_json_state(config.google_sync_state_path, store, utc_now())
    stopped = False

    def stop(signum, frame):  # noqa: ARG001
        nonlocal stopped
        stopped = True
        logger.info("Received signal %s; stopping", signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        while not stopped:
            loop_now = utc_now()
            notifier = SlackNotifier(store, slack, config, loop_now)
            try:
                sync_calendar_once(store, sync_client, notifier, config, loop_now)
            except SyncTokenExpired:
                logger.warning("Sync token expired. Clearing only the token; cached events/reminders remain in SQLite.")
                store.clear_sync_token()
            except Exception:
                logger.exception("Calendar sync failed")

            try:
                reminder_now = utc_now()
                notifier.now = reminder_now
                reconcile_cached_events(store, config, reminder_now)
                process_due_reminders(store, notifier, config, reminder_now)
                store.cleanup_old_state(reminder_now - timedelta(days=HISTORY_RETENTION_DAYS))
                store.write_heartbeat(utc_now())
            except Exception:
                logger.exception("Reminder processing failed")

            for _ in range(config.poll_seconds):
                if stopped:
                    break
                time.sleep(1)
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
