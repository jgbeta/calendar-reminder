from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SyncTokenExpired(RuntimeError):
    """Raised when Google returns HTTP 410 for an expired/invalid sync token."""


@dataclass(frozen=True)
class SyncResult:
    events: list[dict[str, Any]]
    next_sync_token: str


class CalendarSyncClient:
    """Small wrapper around Google Calendar events.list full/incremental sync."""

    def __init__(self, service: Any, *, max_results: int = 2500, single_events: bool = True):
        self.service = service
        # Keep this compatible with syncToken. Do not include timeMin/timeMax,
        # updatedMin, q, orderBy, iCalUID, private/sharedExtendedProperty.
        self.base_params = {
            "maxResults": max_results,
            "singleEvents": single_events,
            "showDeleted": True,
        }

    def full_sync(self, calendar_id: str = "primary") -> SyncResult:
        """Run a full sync and return all pages plus the final nextSyncToken."""

        return self._list_all(calendar_id=calendar_id, sync_token=None)

    def incremental_sync(self, sync_token: str, calendar_id: str = "primary") -> SyncResult:
        """Run an incremental sync using a previously stored nextSyncToken."""

        return self._list_all(calendar_id=calendar_id, sync_token=sync_token)

    def _list_all(self, *, calendar_id: str, sync_token: str | None) -> SyncResult:
        events: list[dict[str, Any]] = []
        next_page_token = None
        next_sync_token = None

        while True:
            params = dict(self.base_params)
            params["calendarId"] = calendar_id
            if sync_token:
                params["syncToken"] = sync_token
            if next_page_token:
                params["pageToken"] = next_page_token

            try:
                response = self.service.events().list(**params).execute()
            except Exception as exc:
                if is_http_410(exc):
                    raise SyncTokenExpired("Google Calendar sync token expired; run a full sync") from exc
                raise

            events.extend(response.get("items", []))
            next_page_token = response.get("nextPageToken")

            if not next_page_token:
                next_sync_token = response.get("nextSyncToken")
                break

        if not next_sync_token:
            raise RuntimeError("Google Calendar response did not include nextSyncToken on the final page")

        logger.info("Calendar sync returned %s events; next_sync_token present", len(events))
        return SyncResult(events=events, next_sync_token=next_sync_token)


def is_http_410(exc: Exception) -> bool:
    """Detect googleapiclient.errors.HttpError with status 410 without importing it."""

    response = getattr(exc, "resp", None) or getattr(exc, "response", None)
    status = getattr(response, "status", None) or getattr(response, "status_code", None)
    return status == 410
