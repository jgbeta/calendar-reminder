from __future__ import annotations

from pathlib import Path
from typing import Sequence


def load_google_credentials(
    scopes: Sequence[str],
    credentials_path: str | Path,
    token_path: str | Path,
    *,
    headless: bool = True,
):
    """Load, refresh, or bootstrap Google OAuth credentials.

    In headless mode this function never opens a browser. It either loads a
    valid token, refreshes an expired token, or raises a clear error.
    """

    # Imports are inside the function so the rest of the kit can be tested
    # without Google dependencies installed.
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    credentials_path = Path(credentials_path)
    token_path = Path(token_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), list(scopes))

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if headless:
        raise RuntimeError(
            f"Missing or invalid Google OAuth token at {token_path}. "
            "Run scripts/bootstrap_google_token.py once on a machine with a browser, "
            "then mount the resulting token.json into the container."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), list(scopes))
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds
