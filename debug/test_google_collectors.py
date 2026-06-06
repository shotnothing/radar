import importlib.util
import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
MAIL_COLLECTOR_PATH = REPO_ROOT / "builtin" / "collector" / "google_mail" / "collector.py"
CALENDAR_COLLECTOR_PATH = REPO_ROOT / "builtin" / "collector" / "google_calendar" / "collector.py"
FIXTURE_START_MS = 1780718400000


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_radar_home():
    configured = os.environ.get("RADAR_HOME", "~/.radar")
    return Path(os.path.expandvars(configured)).expanduser().resolve()


def read_written_events(work_dir, started_at):
    files = sorted(
        path
        for path in Path(work_dir).glob("**/*.jsonl")
        if path.stat().st_mtime >= started_at
    )
    events = []
    for path in files:
        with path.open(encoding="utf-8") as data_file:
            for line in data_file:
                events.append(json.loads(line))
    return events


def read_written_event_files(work_dir, started_at):
    return sorted(
        path
        for path in Path(work_dir).glob("**/*.jsonl")
        if path.stat().st_mtime >= started_at
    )


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class FakeGoogleApiClient:
    payload = {}
    error = None
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def get(self, endpoint, params=None):
        self.__class__.calls.append({"endpoint": endpoint, "params": params or {}})
        if self.__class__.error:
            raise self.__class__.error
        if endpoint.endswith("/profile"):
            return self.__class__.payload.get("profile", {})
        for message_id, message in self.__class__.payload.get("message_details", {}).items():
            if endpoint.endswith(f"/{message_id}"):
                return message
        return self.__class__.payload


def mail_args():
    return SimpleNamespace(
        google_api_base_url="http://127.0.0.1:8888",
        google_api_token="",
        sent_endpoint="/api/google_gmail/users/me/messages",
        message_endpoint="/api/google_gmail/users/me/messages/{id}",
        profile_endpoint="/api/google_gmail/users/me/profile",
        request_timeout=10,
        checkpoint_lookback_seconds=300,
        initial_lookback_seconds=9999999999,
        batch_limit=100,
        force_all=True,
    )


def calendar_args():
    return SimpleNamespace(
        google_api_base_url="http://127.0.0.1:8888",
        google_api_token="",
        organized_events_endpoint="/api/google_calendar/calendars/primary/events",
        request_timeout=10,
        checkpoint_lookback_seconds=300,
        initial_lookback_seconds=9999999999,
        future_lookahead_seconds=2592000,
        batch_limit=100,
        force_all=True,
    )


def test_mail_collector():
    collector = load_module("google_mail_collector", MAIL_COLLECTOR_PATH)
    original_client = collector.LocalGoogleApiClient
    collector.LocalGoogleApiClient = FakeGoogleApiClient
    try:
        radar_home = resolve_radar_home()
        work_dir = radar_home / "collectors" / "google_mail"
        shutil.rmtree(work_dir, ignore_errors=True)
        FakeGoogleApiClient.calls = []
        FakeGoogleApiClient.error = None
        FakeGoogleApiClient.payload = {
            "profile": {"emailAddress": "me@example.com"},
            "messages": [
                {"id": "m1", "threadId": "t1"},
                {"id": "m2", "threadId": "t2"},
            ],
            "message_details": {
                "m1": {
                    "id": "m1",
                    "threadId": "t1",
                    "internalDate": str(FIXTURE_START_MS),
                    "snippet": "Can you confirm?",
                    "labelIds": ["SENT"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Follow up"},
                            {"name": "From", "value": "Me <me@example.com>"},
                            {"name": "To", "value": "You <you@example.com>"},
                        ],
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {
                                    "data": "Q2FuIHlvdSBjb25maXJtIHRoZSBsYXVuY2ggZGF0ZT8="
                                },
                            }
                        ],
                    },
                },
                "m2": {
                    "id": "m2",
                    "threadId": "t2",
                    "internalDate": str(FIXTURE_START_MS + 1000),
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Decision"},
                            {"name": "From", "value": "me@example.com"},
                            {"name": "To", "value": "team@example.com"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": "SSB3aWxsIHRha2UgdGhpcy4="},
                    },
                },
            },
        }

        args = mail_args()
        started_at = time.time()
        first = collector.scan_sources(args, work_dir)
        events = read_written_events(work_dir, started_at)
        event_files = read_written_event_files(work_dir, started_at)
        require(first["messages_seen"] == 2, f"expected two seen messages: {first}")
        require(first["events_written"] == 2, f"expected two written messages: {first}")
        require(len(events) == 2, f"unexpected mail event count: {len(events)}")
        require(event_files, "mail collector should write JSONL event files")
        require(
            all("artifacts" not in path.parts for path in event_files),
            f"mail JSONL must not be written under artifacts/: {event_files}",
        )
        require(
            all(path.parent.parent == work_dir for path in event_files),
            f"mail JSONL should be directly under RADAR_HOME collector day folders: {event_files}",
        )
        require(
            FakeGoogleApiClient.calls[0]["endpoint"] == "/api/google_gmail/users/me/messages",
            f"wrong mail endpoint: {FakeGoogleApiClient.calls}",
        )
        require(
            FakeGoogleApiClient.calls[0]["params"]["labelIds"] == "SENT",
            f"mail collector should pass the Gmail SENT label: {FakeGoogleApiClient.calls}",
        )

        first_event = next(event for event in events if event["extra_data"]["google_mail"]["id"] == "m1")
        require(first_event["subject"]["kind"] == "communication_user_email", "wrong mail kind")
        require(
            first_event["content"]["text"].strip() == "Can you confirm the launch date?",
            f"wrong mail body: {first_event['content']['text']!r}",
        )
        require(first_event["context"]["account_email"] == "me@example.com", "missing account email")
        require("you@example.com" in first_event["context"]["to"][0]["email"], "missing recipient")
        require(first_event["provenance"]["source_uri"] == "gmail://message/m1", "missing mail source URI")

        args.force_all = False
        second = collector.scan_sources(args, work_dir)
        require(second["events_written"] == 0, f"mail scan should dedupe: {second}")
    finally:
        collector.LocalGoogleApiClient = original_client


def test_calendar_collector():
    collector = load_module("google_calendar_collector", CALENDAR_COLLECTOR_PATH)
    original_client = collector.LocalGoogleApiClient
    collector.LocalGoogleApiClient = FakeGoogleApiClient
    try:
        radar_home = resolve_radar_home()
        work_dir = radar_home / "collectors" / "google_calendar"
        shutil.rmtree(work_dir, ignore_errors=True)
        FakeGoogleApiClient.calls = []
        FakeGoogleApiClient.error = None
        FakeGoogleApiClient.payload = {
            "account_email": "me@example.com",
            "events": [
                {
                    "id": "e1",
                    "calendar_id": "primary",
                    "updated_ms": FIXTURE_START_MS,
                    "summary": "Launch sync",
                    "description": "Finalize release plan.",
                    "start": {"dateTime": "2026-06-06T10:00:00Z"},
                    "end": {"dateTime": "2026-06-06T10:30:00Z"},
                    "organizer": {"email": "me@example.com", "displayName": "Me", "self": True},
                    "attendees": [
                        {"email": "you@example.com", "responseStatus": "accepted"}
                    ],
                    "htmlLink": "https://calendar.google.com/event?eid=e1",
                },
                {
                    "id": "e2",
                    "calendar_id": "primary",
                    "updated_ms": FIXTURE_START_MS + 1000,
                    "summary": "Invited sync",
                    "organizer": {"email": "other@example.com"},
                },
            ],
        }

        args = calendar_args()
        started_at = time.time()
        first = collector.scan_sources(args, work_dir)
        events = read_written_events(work_dir, started_at)
        event_files = read_written_event_files(work_dir, started_at)
        require(first["events_seen"] == 2, f"expected two seen calendar events: {first}")
        require(first["events_written"] == 1, f"expected one organized event: {first}")
        require(len(events) == 1, f"unexpected calendar event count: {len(events)}")
        require(event_files, "calendar collector should write JSONL event files")
        require(
            all("artifacts" not in path.parts for path in event_files),
            f"calendar JSONL must not be written under artifacts/: {event_files}",
        )
        require(
            all(path.parent.parent == work_dir for path in event_files),
            f"calendar JSONL should be directly under RADAR_HOME collector day folders: {event_files}",
        )
        require(
            FakeGoogleApiClient.calls[0]["endpoint"] == "/api/google_calendar/calendars/primary/events",
            f"wrong calendar endpoint: {FakeGoogleApiClient.calls}",
        )
        require(
            "timeMin" in FakeGoogleApiClient.calls[0]["params"],
            f"calendar collector should pass Google timeMin: {FakeGoogleApiClient.calls}",
        )

        event = events[0]
        require(event["subject"]["kind"] == "calendar_user_organized_meeting", "wrong calendar kind")
        require(event["content"]["text"] == "Finalize release plan.", "wrong event description")
        require(event["context"]["organizer"]["self"], "organizer should be self")
        require(event["context"]["attendees"][0]["email"] == "you@example.com", "missing attendee")
        require(event["provenance"]["source_uri"].endswith("e1"), "missing calendar source URI")

        args.force_all = False
        second = collector.scan_sources(args, work_dir)
        require(second["events_written"] == 0, f"calendar scan should dedupe: {second}")
    finally:
        collector.LocalGoogleApiClient = original_client


def test_auth_required_status():
    collector = load_module("google_mail_auth_collector", MAIL_COLLECTOR_PATH)
    original_client = collector.LocalGoogleApiClient
    collector.LocalGoogleApiClient = FakeGoogleApiClient
    try:
        radar_home = resolve_radar_home()
        work_dir = radar_home / "collectors" / "google_mail_auth"
        shutil.rmtree(work_dir, ignore_errors=True)
        FakeGoogleApiClient.calls = []
        FakeGoogleApiClient.payload = {}
        FakeGoogleApiClient.error = collector.LocalGoogleApiError(
            "unauthorized",
            status_code=401,
            auth_required=True,
        )
        result = collector.scan_sources(mail_args(), work_dir)
        require(result["needs_auth"], f"expected needs_auth: {result}")
        require(
            not result["permissions"]["google_mail_authorized"],
            f"expected unauthorized permission: {result}",
        )
    finally:
        FakeGoogleApiClient.error = None
        collector.LocalGoogleApiClient = original_client


def main():
    test_mail_collector()
    test_calendar_collector()
    test_auth_required_status()
    print("google collector tests passed")


if __name__ == "__main__":
    main()
