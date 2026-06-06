import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import socketio

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from builtin.collector.google_api import (
    LocalGoogleApiClient,
    LocalGoogleApiError,
    compact_text,
    env_float,
    env_int,
    iso_from_epoch_ms,
    item_list,
    load_checkpoint,
    observed_time_ms,
    parse_time_ms,
    safe_list,
    save_checkpoint,
    write_events,
)


COLLECTOR_ID = "google.calendar"
DISPLAY_NAME = "Google Calendar Collector"
CHECKPOINT_KEY = "google_calendar"
PROCESSED_ID_LIMIT = 10000


def first_value(mapping, *keys, default=""):
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) not in (None, ""):
            return mapping[key]
    return default


def event_id(event):
    return str(first_value(event, "id", "event_id", "iCalUID", "ical_uid", default=str(uuid.uuid4())))


def event_updated_at_ms(event):
    for key in ("updated_ms", "updated_at_ms", "updated", "modified_at"):
        value = parse_time_ms(event.get(key), 0)
        if value:
            return value
    return event_start_ms(event) or observed_time_ms()


def event_start_ms(event):
    start = event.get("start") if isinstance(event.get("start"), dict) else {}
    for value in (
        first_value(start, "dateTime", "date_time", "date"),
        first_value(event, "start_at", "start_time", "start_ms"),
    ):
        parsed = parse_time_ms(value, 0)
        if parsed:
            return parsed
    return 0


def event_end_ms(event):
    end = event.get("end") if isinstance(event.get("end"), dict) else {}
    for value in (
        first_value(end, "dateTime", "date_time", "date"),
        first_value(event, "end_at", "end_time", "end_ms"),
    ):
        parsed = parse_time_ms(value, 0)
        if parsed:
            return parsed
    return 0


def account_email_from_payload(payload):
    if not isinstance(payload, dict):
        return ""
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    return str(
        first_value(
            payload,
            "account_email",
            "user_email",
            "email",
            default=first_value(profile, "emailAddress", "email", default=""),
        )
    )


def email_from_person(value):
    if isinstance(value, dict):
        return str(first_value(value, "email", "address", "mail"))
    return str(value or "")


def is_user_organized(event, account_email=""):
    if event.get("organized_by_user") is True or event.get("organizer_self") is True:
        return True
    organizer = event.get("organizer") if isinstance(event.get("organizer"), dict) else {}
    if organizer.get("self") is True:
        return True
    organizer_email = email_from_person(organizer or first_value(event, "organizer_email"))
    if account_email and organizer_email.lower() == account_email.lower():
        return True
    return not organizer_email and account_email == ""


def normalize_attendee(value):
    if isinstance(value, dict):
        return {
            "email": str(first_value(value, "email", "address", "mail")),
            "name": str(first_value(value, "displayName", "display_name", "name")),
            "response_status": str(first_value(value, "responseStatus", "response_status")),
            "optional": bool(value.get("optional", False)),
            "self": bool(value.get("self", False)),
        }
    return {
        "email": str(value or ""),
        "name": "",
        "response_status": "",
        "optional": False,
        "self": False,
    }


def build_organized_meeting_event(event, account_email=""):
    cal_event_id = event_id(event)
    start_ms = event_start_ms(event)
    end_ms = event_end_ms(event)
    updated_ms = event_updated_at_ms(event)
    observed_at = start_ms or updated_ms
    summary = str(first_value(event, "summary", "title", default="(no title)"))
    description = str(first_value(event, "description", "notes", default=""))
    html_link = str(first_value(event, "htmlLink", "html_link", "url", default=f"gcal://event/{cal_event_id}"))
    calendar_id = str(first_value(event, "calendar_id", "calendarId", default="primary"))
    organizer = event.get("organizer") if isinstance(event.get("organizer"), dict) else {}
    attendees = [
        normalize_attendee(item)
        for item in safe_list(first_value(event, "attendees", "participants", default=[]))
        if item
    ]

    return {
        "id": str(uuid.uuid4()),
        "collector_id": COLLECTOR_ID,
        "source": {
            "type": "calendar",
            "app": "Google Calendar",
            "format": "local_google_api",
        },
        "time": {
            "observed_at": observed_at,
        },
        "anchor": {
            "id": f"gcal:{cal_event_id}",
            "type": "calendar_event",
            "name": "meeting_organized",
            "occurred_at": iso_from_epoch_ms(observed_at),
            "target": {
                "app": "Google Calendar",
                "calendar_id": calendar_id,
                "event_id": cal_event_id,
                "summary": summary,
            },
        },
        "subject": {
            "kind": "calendar_user_organized_meeting",
            "title": f"Organized meeting: {compact_text(summary, 120)}",
        },
        "content": {
            "text": description,
        },
        "context": {
            "account_email": account_email,
            "calendar_id": calendar_id,
            "summary": summary,
            "start_at": iso_from_epoch_ms(start_ms) if start_ms else "",
            "end_at": iso_from_epoch_ms(end_ms) if end_ms else "",
            "updated_at": iso_from_epoch_ms(updated_ms) if updated_ms else "",
            "organizer": {
                "email": email_from_person(organizer),
                "name": str(first_value(organizer, "displayName", "display_name", "name")),
                "self": bool(organizer.get("self", False)),
            },
            "attendees": attendees,
        },
        "provenance": {
            "source_uri": html_link,
            "source_type": "google_calendar_local_api",
            "session_key": f"gcal:{calendar_id}:{cal_event_id}",
            "session_id": cal_event_id,
            "source_message_ids": [f"gcal:{cal_event_id}"],
        },
        "artifacts": [],
        "extra_data": {
            "google_calendar": {
                "id": cal_event_id,
                "calendar_id": calendar_id,
                "ical_uid": str(first_value(event, "iCalUID", "ical_uid")),
                "status": str(first_value(event, "status")),
                "hangout_link": str(first_value(event, "hangoutLink", "hangout_link")),
                "relation_to_user": "organized_by_user",
            }
        },
        "privacy": {
            "contains_raw_content": bool(description),
            "scope": "user_organized_meeting",
        },
    }


def bounded_processed_ids(processed_ids, events):
    keep = list(processed_ids)
    for event in events:
        key = f"gcal:{event_id(event)}"
        if key not in processed_ids:
            processed_ids.add(key)
            keep.append(key)
    if len(keep) > PROCESSED_ID_LIMIT:
        keep = keep[-PROCESSED_ID_LIMIT:]
    return keep


def scan_sources(args, work_dir):
    checkpoint = load_checkpoint(work_dir, CHECKPOINT_KEY)
    state = checkpoint.setdefault(CHECKPOINT_KEY, {})
    previous_last_seen = int(state.get("last_seen_updated_at_ms") or 0)
    now_ms = observed_time_ms()
    if args.force_all:
        updated_min = 0
    elif previous_last_seen:
        updated_min = max(0, previous_last_seen - int(args.checkpoint_lookback_seconds) * 1000)
    else:
        updated_min = max(0, now_ms - int(args.initial_lookback_seconds) * 1000)

    client = LocalGoogleApiClient(
        base_url=args.google_api_base_url,
        token=args.google_api_token,
        timeout=args.request_timeout,
    )
    events_seen = []
    output_events = []
    error = ""
    auth_required = False
    try:
        payload = client.get(
            args.organized_events_endpoint,
            {
                "updated_min_ms": updated_min,
                "time_min_ms": max(0, now_ms - int(args.initial_lookback_seconds) * 1000),
                "time_max_ms": now_ms + int(args.future_lookahead_seconds) * 1000,
                "limit": int(args.batch_limit),
            },
        )
        events_seen = item_list(payload, "events", "meetings", "items")
        account_email = account_email_from_payload(payload)
        processed_ids = set(state.get("processed_event_ids") or [])
        for event in events_seen:
            if not isinstance(event, dict):
                continue
            event_key = f"gcal:{event_id(event)}"
            if not is_user_organized(event, account_email):
                continue
            if not args.force_all and event_key in processed_ids:
                continue
            output_events.append(build_organized_meeting_event(event, account_email))
    except LocalGoogleApiError as api_error:
        error = str(api_error)
        auth_required = api_error.auth_required

    output_path = write_events(work_dir, output_events)
    valid_seen = [event for event in events_seen if isinstance(event, dict)]
    if valid_seen:
        latest = max(valid_seen, key=event_updated_at_ms)
        state["last_seen_updated_at_ms"] = event_updated_at_ms(latest)
    state["processed_event_ids"] = bounded_processed_ids(
        set(state.get("processed_event_ids") or []),
        valid_seen,
    )
    state["last_scan_at"] = observed_time_ms()
    state["last_error"] = error
    save_checkpoint(work_dir, checkpoint)

    return {
        "permissions": {
            "local_google_api_reachable": not error or auth_required,
            "google_calendar_authorized": not auth_required and not error,
        },
        "events_seen": len(events_seen),
        "events_changed": len(output_events),
        "events_written": len(output_events),
        "output_path": str(output_path) if output_path else "",
        "last_error": error,
        "needs_auth": auth_required,
    }


def register_collector(client):
    return client.call(
        "collector:register",
        {
            "collector_id": COLLECTOR_ID,
            "protocol_version": 1,
            "capabilities": [
                "google_calendar_organized_events",
                "local_google_api",
                "incremental_scan",
                "source_provenance",
                "heartbeat",
            ],
            "metadata": {
                "display_name": DISPLAY_NAME,
            },
        },
        timeout=5,
    )


def connect_client(client, coordinator_url, timeout):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            client.connect(coordinator_url)
            return
        except socketio.exceptions.ConnectionError as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"could not connect to coordinator: {last_error}")


def run(args):
    client = socketio.Client()
    connect_client(client, args.coordinator_url, args.connect_timeout)
    try:
        registration = register_collector(client)
        if not registration.get("ok"):
            raise RuntimeError(f"collector registration failed: {registration}")
        work_dir = registration["work_dir"]
        print(f"registered {COLLECTOR_ID}")

        last_scan = {}
        deadline = time.monotonic() + max(args.duration, 0)
        next_scan_at = 0
        while True:
            now = time.monotonic()
            if now >= next_scan_at:
                started_at = observed_time_ms()
                last_scan = scan_sources(args, work_dir)
                last_scan["last_scan_started_at"] = started_at
                if last_scan.get("output_path"):
                    print(f"wrote collected Google Calendar data: {last_scan['output_path']}")
                next_scan_at = now + max(args.scan_interval, 1)

            client.call(
                "collector:heartbeat",
                {
                    "status": "needs_auth"
                    if last_scan.get("needs_auth")
                    else "error"
                    if last_scan.get("last_error")
                    else "ok",
                    "permissions": last_scan.get("permissions", {}),
                    "last_scan_started_at": last_scan.get("last_scan_started_at", 0),
                    "last_scan_completed_at": observed_time_ms(),
                    "events_seen": last_scan.get("events_seen", 0),
                    "events_changed": last_scan.get("events_changed", 0),
                    "events_written": last_scan.get("events_written", 0),
                    "last_write": last_scan.get("output_path", ""),
                    "last_error": last_scan.get("last_error", ""),
                },
                timeout=5,
            )

            if args.duration <= 0 or time.monotonic() >= deadline:
                break
            sleep_for = min(
                max(args.heartbeat_interval, 0.1),
                max(deadline - time.monotonic(), 0),
            )
            if sleep_for:
                time.sleep(sleep_for)
    finally:
        client.disconnect()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Register a Radar Google Calendar collector and write user-organized meeting events."
    )
    parser.add_argument(
        "--coordinator-url",
        default=os.environ.get("RADAR_COORDINATOR_URL", "http://localhost:5000"),
        help="Socket.IO coordinator URL.",
    )
    parser.add_argument(
        "--google-api-base-url",
        default=os.environ.get("RADAR_GOOGLE_API_BASE_URL", "http://127.0.0.1:47611"),
        help="Local desktop Google API bridge base URL.",
    )
    parser.add_argument(
        "--organized-events-endpoint",
        default=os.environ.get(
            "RADAR_GOOGLE_CALENDAR_ORGANIZED_EVENTS_ENDPOINT",
            "/google_calendar/events/organized",
        ),
        help="Local API path that returns meetings organized by the user.",
    )
    parser.add_argument(
        "--google-api-token",
        default=os.environ.get("RADAR_GOOGLE_API_TOKEN", ""),
        help="Optional bearer token for the local Google API bridge.",
    )
    parser.add_argument(
        "--duration",
        default=env_float("RADAR_GOOGLE_CALENDAR_COLLECTOR_DURATION", 10),
        type=float,
        help="Seconds to stay registered before disconnecting. Use <= 0 to run forever.",
    )
    parser.add_argument(
        "--scan-interval",
        default=env_float("RADAR_GOOGLE_CALENDAR_SCAN_INTERVAL", 300),
        type=float,
        help="Seconds between Google Calendar scans.",
    )
    parser.add_argument(
        "--heartbeat-interval",
        default=env_float("RADAR_GOOGLE_CALENDAR_HEARTBEAT_INTERVAL", 2),
        type=float,
        help="Seconds between collector heartbeats while registered.",
    )
    parser.add_argument(
        "--connect-timeout",
        default=env_float("RADAR_COLLECTOR_CONNECT_TIMEOUT", 10),
        type=float,
        help="Seconds to wait for the coordinator before failing.",
    )
    parser.add_argument(
        "--request-timeout",
        default=env_float("RADAR_GOOGLE_API_REQUEST_TIMEOUT", 10),
        type=float,
        help="Seconds to wait for local Google API responses.",
    )
    parser.add_argument(
        "--checkpoint-lookback-seconds",
        default=env_int("RADAR_GOOGLE_CALENDAR_CHECKPOINT_LOOKBACK_SECONDS", 300),
        type=int,
        help="Overlap before last checkpoint used to catch late-synced calendar changes.",
    )
    parser.add_argument(
        "--initial-lookback-seconds",
        default=env_int("RADAR_GOOGLE_CALENDAR_INITIAL_LOOKBACK_SECONDS", 86400),
        type=int,
        help="First-run organized-meeting lookback window.",
    )
    parser.add_argument(
        "--future-lookahead-seconds",
        default=env_int("RADAR_GOOGLE_CALENDAR_FUTURE_LOOKAHEAD_SECONDS", 2592000),
        type=int,
        help="How far into the future to scan for organized meetings.",
    )
    parser.add_argument(
        "--batch-limit",
        default=env_int("RADAR_GOOGLE_CALENDAR_BATCH_LIMIT", 100),
        type=int,
        help="Maximum organized meetings to process per scan.",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        default=False,
        help="Scan from the beginning and re-emit events not blocked by current dedupe state.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
