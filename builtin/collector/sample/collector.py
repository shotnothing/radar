import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import socketio


COLLECTOR_ID = "builtin.sample"
DISPLAY_NAME = "Sample Collector"


def observed_time_ms():
    return int(time.time() * 1000)


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_sample_event(sample_text):
    observed_at = observed_time_ms()
    occurred_at = utc_timestamp()
    return {
        "id": str(uuid.uuid4()),
        "collector_id": COLLECTOR_ID,
        "source": {
            "type": "sample",
            "app": "Radar Debug",
        },
        "time": {
            "observed_at": observed_at,
        },
        "anchor": {
            "id": f"sample_anchor_{observed_at}",
            "type": "debug_event",
            "name": "sample_collect",
            "occurred_at": occurred_at,
            "target": {
                "app": "Radar Debug",
                "window_title": "Sample Collector",
            },
        },
        "subject": {
            "kind": "debug_sample",
            "title": "Sample collected data",
        },
        "content": {
            "text": sample_text,
        },
        "context": {
            "active_app": "Radar Debug",
            "active_window_title": "Sample Collector",
            "user_action": "debug_sample_created",
        },
        "artifacts": [],
        "extra_data": {
            "sample": {
                "created_by": DISPLAY_NAME,
                "purpose": "collector_registration_and_storage_test",
            },
        },
    }


def write_sample_event(work_dir, sample_text):
    event = build_sample_event(sample_text)
    observed_at = event["time"]["observed_at"]
    day_dir = Path(work_dir) / datetime.fromtimestamp(
        observed_at / 1000
    ).strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    output_path = day_dir / f"{observed_at}.jsonl"
    with output_path.open("a", encoding="utf-8") as output_file:
        output_file.write(json.dumps(event, ensure_ascii=True, sort_keys=True))
        output_file.write("\n")
    return output_path


def register_collector(client):
    return client.call(
        "collector:register",
        {
            "collector_id": COLLECTOR_ID,
            "protocol_version": 1,
            "capabilities": ["sample_event", "jsonl_writer", "heartbeat"],
            "metadata": {
                "display_name": DISPLAY_NAME,
            },
        },
        timeout=5,
    )


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


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

        output_path = write_sample_event(
            registration["work_dir"],
            args.sample_text,
        )
        print(f"registered {COLLECTOR_ID}")
        print(f"wrote sample collected data: {output_path}")

        deadline = time.monotonic() + max(args.duration, 0)
        while time.monotonic() < deadline:
            client.call(
                "collector:heartbeat",
                {
                    "status": "ok",
                    "permissions": {},
                    "last_write": str(output_path),
                },
                timeout=5,
            )
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
        description="Register a sample Radar collector and write one JSONL event."
    )
    parser.add_argument(
        "--coordinator-url",
        default=os.environ.get("RADAR_COORDINATOR_URL", "http://localhost:5000"),
        help="Socket.IO coordinator URL.",
    )
    parser.add_argument(
        "--duration",
        default=env_float("RADAR_SAMPLE_COLLECTOR_DURATION", 10),
        type=float,
        help="Seconds to stay registered before disconnecting.",
    )
    parser.add_argument(
        "--heartbeat-interval",
        default=env_float("RADAR_SAMPLE_COLLECTOR_HEARTBEAT_INTERVAL", 2),
        type=float,
        help="Seconds between collector heartbeats while registered.",
    )
    parser.add_argument(
        "--sample-text",
        default=os.environ.get(
            "RADAR_SAMPLE_TEXT",
            "Sample collected data from the Radar debug collector.",
        ),
        help="Text to write in the sample collected event.",
    )
    parser.add_argument(
        "--connect-timeout",
        default=env_float("RADAR_COLLECTOR_CONNECT_TIMEOUT", 10),
        type=float,
        help="Seconds to wait for the coordinator before failing.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
