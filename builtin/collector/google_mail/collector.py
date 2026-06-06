import argparse
import base64
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


COLLECTOR_ID = "google.mail"
DISPLAY_NAME = "Google Mail Collector"
CHECKPOINT_KEY = "google_mail"
PROCESSED_ID_LIMIT = 10000


def first_value(mapping, *keys, default=""):
    for key in keys:
        if isinstance(mapping, dict) and mapping.get(key) not in (None, ""):
            return mapping[key]
    return default


def normalize_email_address(value):
    if isinstance(value, dict):
        email = first_value(value, "email", "address", "mail")
        name = first_value(value, "name", "display_name")
        return {"email": str(email), "name": str(name)}
    return {"email": str(value or ""), "name": ""}


def normalize_address_list(value):
    return [normalize_email_address(item) for item in safe_list(value) if item]


def header_map(message):
    raw_headers = message.get("headers")
    if isinstance(raw_headers, dict):
        return {str(key).lower(): str(value) for key, value in raw_headers.items()}
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    headers = {}
    for item in payload.get("headers") or []:
        if isinstance(item, dict) and item.get("name"):
            headers[str(item["name"]).lower()] = str(item.get("value", ""))
    return headers


def header_value(message, name):
    return header_map(message).get(name.lower(), "")


def message_id(message):
    return str(
        first_value(
            message,
            "id",
            "message_id",
            "gmail_id",
            default=header_value(message, "Message-ID"),
        )
        or first_value(
            message,
            "rfc822_message_id",
            default=str(uuid.uuid4()),
        )
    )


def message_sent_at_ms(message):
    for key in ("sent_at_ms", "internal_date_ms", "internalDate", "date_ms", "timestamp_ms"):
        value = parse_time_ms(message.get(key), 0)
        if value:
            return value
    for key in ("sent_at", "date", "timestamp"):
        value = parse_time_ms(message.get(key), 0)
        if value:
            return value
    return observed_time_ms()


def message_text(message):
    body = first_value(message, "body", "text", "plain_text", "snippet")
    if isinstance(body, dict):
        body = first_value(body, "text", "plain", "value")
    if body:
        return str(body)
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    decoded = decode_payload_text(payload)
    if decoded:
        return decoded
    return str(body or "")


def source_uri(message, msg_id):
    return str(first_value(message, "web_link", "url", "source_uri", default=f"gmail://message/{msg_id}"))


def decode_payload_text(payload):
    if not isinstance(payload, dict):
        return ""
    mime_type = str(payload.get("mimeType") or "")
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    data = body.get("data")
    if data and (mime_type.startswith("text/plain") or not payload.get("parts")):
        return decode_base64url_text(data)
    parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []
    plain_parts = []
    html_parts = []
    for part in parts:
        text = decode_payload_text(part)
        part_type = str(part.get("mimeType") or "") if isinstance(part, dict) else ""
        if not text:
            continue
        if part_type.startswith("text/plain"):
            plain_parts.append(text)
        elif part_type.startswith("text/html"):
            html_parts.append(text)
        else:
            plain_parts.append(text)
    return "\n\n".join(plain_parts or html_parts)


def decode_base64url_text(data):
    try:
        padded = str(data) + "=" * (-len(str(data)) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def gmail_after_query(scan_after_ms):
    if not scan_after_ms:
        return ""
    return "after:" + time.strftime("%Y/%m/%d", time.gmtime(scan_after_ms / 1000))


def is_gmail_summary(message):
    payload = message.get("payload") if isinstance(message, dict) else None
    return isinstance(message, dict) and not isinstance(payload, dict)


def hydrate_gmail_messages(client, endpoint_template, summaries):
    messages = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        msg_id = summary.get("id")
        if not msg_id or not is_gmail_summary(summary):
            messages.append(summary)
            continue
        message = client.get(
            endpoint_template.format(id=msg_id),
            {"format": "full"},
        )
        if isinstance(message, dict):
            messages.append(message)
    return messages


def build_sent_email_event(message, account_email=""):
    msg_id = message_id(message)
    sent_at = message_sent_at_ms(message)
    thread_id = str(first_value(message, "thread_id", "threadId", default=""))
    headers = header_map(message)
    subject = str(first_value(message, "subject", default=headers.get("subject", "(no subject)")))
    from_list = normalize_address_list(first_value(message, "from", "sender", default=headers.get("from", "")))
    to_list = normalize_address_list(first_value(message, "to", "recipients", default=headers.get("to", "")))
    cc_list = normalize_address_list(first_value(message, "cc", default=headers.get("cc", "")))
    bcc_list = normalize_address_list(first_value(message, "bcc", default=headers.get("bcc", "")))
    text = message_text(message)
    uri = source_uri(message, msg_id)

    return {
        "id": str(uuid.uuid4()),
        "collector_id": COLLECTOR_ID,
        "source": {
            "type": "email",
            "app": "Google Mail",
            "format": "local_google_api",
        },
        "time": {
            "observed_at": sent_at,
        },
        "anchor": {
            "id": f"gmail:{msg_id}",
            "type": "communication_email",
            "name": "email_sent",
            "occurred_at": iso_from_epoch_ms(sent_at),
            "target": {
                "app": "Google Mail",
                "thread_id": thread_id,
                "subject": subject,
            },
        },
        "subject": {
            "kind": "communication_user_email",
            "title": f"Sent email: {compact_text(subject, 120)}",
        },
        "content": {
            "text": text,
        },
        "context": {
            "account_email": account_email,
            "thread_id": thread_id,
            "from": from_list,
            "to": to_list,
            "cc": cc_list,
            "bcc": bcc_list,
            "snippet": str(first_value(message, "snippet", default="")),
        },
        "provenance": {
            "source_uri": uri,
            "source_type": "google_mail_local_api",
            "session_key": f"gmail:{thread_id or msg_id}",
            "session_id": thread_id or msg_id,
            "source_message_ids": [f"gmail:{msg_id}"],
        },
        "artifacts": [],
        "extra_data": {
            "google_mail": {
                "id": msg_id,
                "thread_id": thread_id,
                "label_ids": safe_list(first_value(message, "label_ids", "labelIds", default=[])),
                "headers": headers,
                "relation_to_user": "sent_by_user",
            }
        },
        "privacy": {
            "contains_raw_content": bool(text),
            "scope": "user_sent_email",
        },
    }


def bounded_processed_ids(processed_ids, messages):
    keep = list(processed_ids)
    for message in messages:
        msg_id = f"gmail:{message_id(message)}"
        if msg_id not in processed_ids:
            processed_ids.add(msg_id)
            keep.append(msg_id)
    if len(keep) > PROCESSED_ID_LIMIT:
        keep = keep[-PROCESSED_ID_LIMIT:]
    return keep


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


def fetch_gmail_profile_email(client, profile_endpoint):
    try:
        profile = client.get(profile_endpoint)
    except LocalGoogleApiError:
        return ""
    if not isinstance(profile, dict):
        return ""
    return str(first_value(profile, "emailAddress", "email", default=""))


def scan_sources(args, work_dir):
    checkpoint = load_checkpoint(work_dir, CHECKPOINT_KEY)
    state = checkpoint.setdefault(CHECKPOINT_KEY, {})
    previous_last_seen = int(state.get("last_seen_sent_at_ms") or 0)
    if args.force_all:
        scan_after = 0
    elif previous_last_seen:
        scan_after = max(0, previous_last_seen - int(args.checkpoint_lookback_seconds) * 1000)
    else:
        scan_after = max(0, observed_time_ms() - int(args.initial_lookback_seconds) * 1000)

    client = LocalGoogleApiClient(
        base_url=args.google_api_base_url,
        token=args.google_api_token,
        timeout=args.request_timeout,
    )
    messages = []
    events = []
    error = ""
    auth_required = False
    try:
        payload = client.get(
            args.sent_endpoint,
            {
                "labelIds": "SENT",
                "maxResults": int(args.batch_limit),
                **({"q": gmail_after_query(scan_after)} if gmail_after_query(scan_after) else {}),
            },
        )
        summaries = item_list(payload, "messages", "emails", "items")
        messages = hydrate_gmail_messages(client, args.message_endpoint, summaries)
        account_email = account_email_from_payload(payload) or fetch_gmail_profile_email(
            client,
            args.profile_endpoint,
        )
        processed_ids = set(state.get("processed_message_ids") or [])
        for message in messages:
            if not isinstance(message, dict):
                continue
            msg_key = f"gmail:{message_id(message)}"
            if not args.force_all and msg_key in processed_ids:
                continue
            events.append(build_sent_email_event(message, account_email))
    except LocalGoogleApiError as api_error:
        error = str(api_error)
        auth_required = api_error.auth_required

    output_path = write_events(work_dir, events)
    if messages:
        latest = max(messages, key=message_sent_at_ms)
        state["last_seen_sent_at_ms"] = message_sent_at_ms(latest)
    state["processed_message_ids"] = bounded_processed_ids(
        set(state.get("processed_message_ids") or []),
        [message for message in messages if isinstance(message, dict)],
    )
    state["last_scan_at"] = observed_time_ms()
    state["last_error"] = error
    save_checkpoint(work_dir, checkpoint)

    return {
        "permissions": {
            "local_google_api_reachable": not error or auth_required,
            "google_mail_authorized": not auth_required and not error,
        },
        "messages_seen": len(messages),
        "messages_changed": len(events),
        "events_written": len(events),
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
                "google_mail_sent",
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
                    print(f"wrote collected Google Mail data: {last_scan['output_path']}")
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
                    "messages_seen": last_scan.get("messages_seen", 0),
                    "messages_changed": last_scan.get("messages_changed", 0),
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
        description="Register a Radar Google Mail collector and write user-sent email events."
    )
    parser.add_argument(
        "--coordinator-url",
        default=os.environ.get("RADAR_COORDINATOR_URL", "http://localhost:5000"),
        help="Socket.IO coordinator URL.",
    )
    parser.add_argument(
        "--google-api-base-url",
        default=os.environ.get("RADAR_GOOGLE_API_BASE_URL", "http://127.0.0.1:8888"),
        help="Local desktop Google API bridge base URL.",
    )
    parser.add_argument(
        "--sent-endpoint",
        default=os.environ.get("RADAR_GOOGLE_MAIL_SENT_ENDPOINT", "/api/google_gmail/users/me/messages"),
        help="Local API path that lists Gmail SENT messages.",
    )
    parser.add_argument(
        "--message-endpoint",
        default=os.environ.get(
            "RADAR_GOOGLE_MAIL_MESSAGE_ENDPOINT",
            "/api/google_gmail/users/me/messages/{id}",
        ),
        help="Local API path template that reads one Gmail message by {id}.",
    )
    parser.add_argument(
        "--profile-endpoint",
        default=os.environ.get(
            "RADAR_GOOGLE_MAIL_PROFILE_ENDPOINT",
            "/api/google_gmail/users/me/profile",
        ),
        help="Local API path that returns the Gmail profile.",
    )
    parser.add_argument(
        "--google-api-token",
        default=os.environ.get("RADAR_GOOGLE_API_TOKEN", ""),
        help="Optional bearer token for the local Google API bridge.",
    )
    parser.add_argument(
        "--duration",
        default=env_float("RADAR_GOOGLE_MAIL_COLLECTOR_DURATION", 10),
        type=float,
        help="Seconds to stay registered before disconnecting. Use <= 0 to run forever.",
    )
    parser.add_argument(
        "--scan-interval",
        default=env_float("RADAR_GOOGLE_MAIL_SCAN_INTERVAL", 300),
        type=float,
        help="Seconds between Google Mail scans.",
    )
    parser.add_argument(
        "--heartbeat-interval",
        default=env_float("RADAR_GOOGLE_MAIL_HEARTBEAT_INTERVAL", 2),
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
        default=env_int("RADAR_GOOGLE_MAIL_CHECKPOINT_LOOKBACK_SECONDS", 300),
        type=int,
        help="Overlap before last checkpoint used to catch late-synced email.",
    )
    parser.add_argument(
        "--initial-lookback-seconds",
        default=env_int("RADAR_GOOGLE_MAIL_INITIAL_LOOKBACK_SECONDS", 86400),
        type=int,
        help="First-run sent-email lookback window.",
    )
    parser.add_argument(
        "--batch-limit",
        default=env_int("RADAR_GOOGLE_MAIL_BATCH_LIMIT", 100),
        type=int,
        help="Maximum sent email messages to process per scan.",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        default=False,
        help="Scan from the beginning and re-emit messages not blocked by current dedupe state.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
