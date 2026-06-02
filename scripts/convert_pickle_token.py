#!/usr/bin/env python3
"""Convert an existing token.pickle (old format) to token.json (new format).

Run this once from inside the project directory using a Python that has
google-auth installed (e.g. the original slackbot virtualenv):

    /home/melzar/slackbot/bin/python3 scripts/convert_pickle_token.py

No browser or new OAuth flow is required — it just re-serializes the credentials
that are already stored in the pickle file.
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert token.pickle to token.json")
    parser.add_argument(
        "--pickle",
        default="../token.pickle",
        help="Path to the existing token.pickle (default: ../token.pickle)",
    )
    parser.add_argument(
        "--out",
        default="data/token.json",
        help="Output path for token.json (default: data/token.json)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh the token before writing (use if the token is expired)",
    )
    args = parser.parse_args()

    pickle_path = Path(args.pickle)
    out_path = Path(args.out)

    if not pickle_path.exists():
        print(f"ERROR: pickle file not found: {pickle_path}", file=sys.stderr)
        return 1

    with pickle_path.open("rb") as f:
        creds = pickle.load(f)

    if args.refresh or (creds.expired and creds.refresh_token):
        print("Refreshing token...")
        from google.auth.transport.requests import Request
        creds.refresh(Request())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Written: {out_path}  (valid={creds.valid}, expired={creds.expired})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
