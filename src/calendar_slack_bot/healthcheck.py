from __future__ import annotations

from datetime import datetime, timezone
import sys

from .config import load_config_from_env
from .state_store import SQLiteStateStore


def load_dotenv_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(override=True)


def main() -> int:
    load_dotenv_file()
    config = load_config_from_env()
    db_path = config.calendar_bot_db_path
    if not db_path.exists():
        print(f"SQLite state DB does not exist: {db_path}", file=sys.stderr)
        return 1

    store = SQLiteStateStore(db_path)
    try:
        heartbeat = store.read_heartbeat()
    finally:
        store.close()

    if heartbeat is None:
        print("SQLite heartbeat is missing", file=sys.stderr)
        return 1

    max_age_seconds = max(config.poll_seconds * 3, 180)
    age_seconds = (datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc)).total_seconds()
    if age_seconds > max_age_seconds:
        print(
            f"SQLite heartbeat is stale: age={age_seconds:.0f}s max={max_age_seconds}s",
            file=sys.stderr,
        )
        return 1

    print(f"SQLite heartbeat ok: age={age_seconds:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
