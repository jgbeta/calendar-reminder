import _path  # noqa: F401
import unittest

from calendar_slack_bot.sync import CalendarSyncClient, SyncTokenExpired


class FakeRequest:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.response


class FakeEvents:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            return FakeRequest(exc=response)
        return FakeRequest(response=response)


class FakeService:
    def __init__(self, responses):
        self.fake_events = FakeEvents(responses)

    def events(self):
        return self.fake_events


class FakeResp:
    status = 410


class FakeHttpError(Exception):
    resp = FakeResp()


class SyncTests(unittest.TestCase):
    def test_paginates_until_next_sync_token(self):
        service = FakeService([
            {"items": [{"id": "1"}], "nextPageToken": "page-2"},
            {"items": [{"id": "2"}], "nextSyncToken": "sync-2"},
        ])
        client = CalendarSyncClient(service)
        result = client.full_sync("primary")
        self.assertEqual([event["id"] for event in result.events], ["1", "2"])
        self.assertEqual(result.next_sync_token, "sync-2")
        self.assertEqual(service.fake_events.calls[1]["pageToken"], "page-2")

    def test_raises_sync_token_expired_on_410(self):
        service = FakeService([FakeHttpError("gone")])
        client = CalendarSyncClient(service)
        with self.assertRaises(SyncTokenExpired):
            client.incremental_sync("old-sync")


if __name__ == "__main__":
    unittest.main()
