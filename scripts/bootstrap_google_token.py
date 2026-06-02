#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from calendar_slack_bot.auth import load_google_credentials  # noqa: E402
from calendar_slack_bot.config import SCOPES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create/refresh token.json for the Calendar Slack bot.")
    parser.add_argument("--credentials", default="credentials.json", help="OAuth client credentials JSON")
    parser.add_argument("--token", default="token.json", help="Output token JSON path")
    args = parser.parse_args()

    creds = load_google_credentials(
        SCOPES,
        credentials_path=args.credentials,
        token_path=args.token,
        headless=False,
    )
    print(f"Token written to {args.token}. Valid={creds.valid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
