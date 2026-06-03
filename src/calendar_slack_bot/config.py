from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class BotConfig:
    calendar_id: str
    poll_seconds: int
    google_credentials_path: Path
    google_token_path: Path
    google_sync_state_path: Path
    calendar_bot_db_path: Path
    slack_bot_token: str | None
    slack_dm_channel_id: str
    headless: bool
    ignored_creator_emails: frozenset
    slack_mention_user_id: str | None


SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def load_config_from_env() -> BotConfig:
    raw_emails = os.getenv("IGNORED_CREATOR_EMAILS", "")
    ignored = frozenset(e.strip().lower() for e in raw_emails.split(",") if e.strip())
    return BotConfig(
        calendar_id=os.getenv("CALENDAR_ID", "primary"),
        poll_seconds=int(os.getenv("POLL_SECONDS", "60")),
        google_credentials_path=Path(os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")),
        google_token_path=Path(os.getenv("GOOGLE_TOKEN_PATH", "token.json")),
        google_sync_state_path=Path(os.getenv("GOOGLE_SYNC_STATE_PATH", "google-sync-state.json")),
        calendar_bot_db_path=Path(os.getenv("CALENDAR_BOT_DB_PATH", "data/calendar-bot.sqlite")),
        slack_bot_token=read_secret_env_or_file("SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN_FILE"),
        slack_dm_channel_id=os.getenv("SLACK_DM_CHANNEL_ID", ""),
        headless=os.getenv("HEADLESS", "true").lower() in {"1", "true", "yes", "y"},
        ignored_creator_emails=ignored,
        slack_mention_user_id=os.getenv("SLACK_MENTION_USER_ID") or None,
    )


def read_secret_env_or_file(env_name: str, file_env_name: str) -> str | None:
    if os.getenv(env_name):
        return os.getenv(env_name)
    file_path = os.getenv(file_env_name)
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return None
