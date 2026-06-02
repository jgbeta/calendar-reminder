from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SlackMessenger:
    """Thin Slack Web API wrapper with explicit ok/error checks."""

    def __init__(self, token: str):
        if not token:
            raise ValueError("Slack bot token is required")
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError

        self._client = WebClient(token=token)
        self._slack_api_error = SlackApiError

    def post_message(self, channel: str, text: str) -> dict:
        if not channel:
            raise ValueError("Slack channel/DM id is required")
        try:
            response = self._client.chat_postMessage(channel=channel, text=text)
            data = response.data
        except self._slack_api_error as exc:
            logger.exception("Slack chat.postMessage failed: %s", exc.response.get("error"))
            raise

        if not data.get("ok", False):
            raise RuntimeError(f"Slack chat.postMessage returned ok=false: {data.get('error')}")
        return data

    def open_dm(self, user_id: str) -> str:
        """Open a DM with a user and return the D... channel id."""

        response = self._client.conversations_open(users=user_id)
        data = response.data
        if not data.get("ok", False):
            raise RuntimeError(f"Slack conversations.open returned ok=false: {data.get('error')}")
        return data["channel"]["id"]
