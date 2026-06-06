"""Runtime for the standalone Chrome collector."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .coordinator import CoordinatorClient
from .observation import CHROME_COLLECTOR_ID, build_observation, epoch_ms_now, normalize_event_name
from .storage import JsonlObservationStore

IGNORED_EVENT_NAMES = {"focus", "element_focus"}

CAPABILITIES = [
    "active_tab",
    "navigation",
    "browser_event",
    "dom_event",
    "jsonl_storage",
]


@dataclass(frozen=True)
class ChromeTabState:
    window_title: str
    title: str
    url: str


class ChromeStateReader:
    """Read active Chrome tab state on macOS using AppleScript."""

    DELIMITER = "|||RADAR|||"

    def read_active_tab(self) -> ChromeTabState | None:
        if platform.system() != "Darwin":
            return None

        script = f"""
        tell application "System Events"
            if not (exists process "Google Chrome") then return ""
        end tell
        tell application "Google Chrome"
            if (count of windows) is 0 then return ""
            set activeWindow to front window
            set activeTab to active tab of activeWindow
            return (title of activeWindow) & "{self.DELIMITER}" & (title of activeTab) & "{self.DELIMITER}" & (URL of activeTab)
        end tell
        """
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        if result.returncode != 0:
            return None

        output = result.stdout.strip()
        if not output:
            return None

        parts = output.split(self.DELIMITER)
        if len(parts) != 3:
            return None

        window_title, title, url = (part.strip() for part in parts)
        if not url:
            return None
        return ChromeTabState(window_title=window_title, title=title, url=url)


class ChromeCollectorRuntime:
    def __init__(
        self,
        *,
        work_dir: str | Path,
        collector_id: str = CHROME_COLLECTOR_ID,
        split_ms: int = 3_600_000,
        state_reader: ChromeStateReader | None = None,
    ) -> None:
        self.collector_id = collector_id
        self.store = JsonlObservationStore(work_dir, split_ms=split_ms)
        self.state_reader = state_reader or ChromeStateReader()
        self.lock = threading.Lock()
        self.stored_count = 0
        self.last_observation_at: int | None = None
        self.last_file: str | None = None
        self.last_error: str | None = None
        self.paused = False
        self._last_tab_key: tuple[str, str] | None = None
        self._stop = threading.Event()

    def handle_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if self.paused:
            return None

        event_name = normalize_event_name(
            event.get("event_name") or event.get("eventName") or event.get("type")
        )
        if event_name in IGNORED_EVENT_NAMES:
            return None

        observation = build_observation(event, collector_id=self.collector_id)
        path = self.store.write(observation)
        with self.lock:
            self.stored_count += 1
            self.last_observation_at = observation["time"]["observed_at"]
            self.last_file = str(path)
            self.last_error = None
        return observation

    def poll_once(self) -> dict[str, Any] | None:
        state = self.state_reader.read_active_tab()
        if state is None:
            return None

        tab_key = (state.title, state.url)
        if tab_key == self._last_tab_key:
            return None

        self._last_tab_key = tab_key
        return self.handle_event(
            {
                "event_name": "navigation",
                "observed_at": epoch_ms_now(),
                "document_title": state.title,
                "document_url": state.url,
                "window_title": state.window_title,
            }
        )

    def status_payload(self) -> dict[str, Any]:
        with self.lock:
            return {
                "status": "paused" if self.paused else ("degraded" if self.last_error else "ok"),
                "permissions": {
                    "macos_automation_google_chrome": "unknown",
                    "chrome_extension": "unknown",
                },
                "stored_count": self.stored_count,
                "last_observation_at": self.last_observation_at,
                "last_file": self.last_file,
                "last_error": self.last_error,
            }

    def set_error(self, message: str) -> None:
        with self.lock:
            self.last_error = message

    def stop(self) -> None:
        self._stop.set()

    def run_poll_loop(self, interval_seconds: float) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # pragma: no cover - defensive runtime guard.
                self.set_error(str(exc))
            self._stop.wait(interval_seconds)


def make_handler(runtime: ChromeCollectorRuntime) -> type[BaseHTTPRequestHandler]:
    class ChromeEventHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_json(200, runtime.status_payload())
                return
            self.send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/event":
                self.send_json(404, {"error": "not_found"})
                return

            try:
                body = self.read_json_body()
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return

            events = body if isinstance(body, list) else [body]
            if not all(isinstance(event, dict) for event in events):
                self.send_json(400, {"error": "body must be an object or array of objects"})
                return

            observations = [
                observation
                for event in events
                if (observation := runtime.handle_event(event)) is not None
            ]
            self.send_json(200, {"ok": True, "stored": len(observations)})

        def read_json_body(self) -> Any:
            length_header = self.headers.get("Content-Length")
            if not length_header:
                raise ValueError("missing request body")
            try:
                length = int(length_header)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("invalid JSON body") from exc

        def send_json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ChromeEventHandler


def run_event_server(
    runtime: ChromeCollectorRuntime,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run(args: argparse.Namespace) -> None:
    coordinator = CoordinatorClient(
        args.coordinator_url,
        collector_id=args.collector_id,
        capabilities=CAPABILITIES,
        metadata={
            "display_name": "Chrome Browser Collector",
            "event_url": f"http://{args.event_host}:{args.event_port}/event",
        },
    )
    work_dir = coordinator.connect_and_register(
        connect_timeout_seconds=args.connect_timeout,
    )
    runtime = ChromeCollectorRuntime(
        work_dir=work_dir,
        collector_id=args.collector_id,
        split_ms=args.file_split_ms,
    )
    coordinator.start_heartbeat_loop(runtime.status_payload, args.heartbeat_interval)

    event_server = run_event_server(runtime, args.event_host, args.event_port)
    poll_thread: threading.Thread | None = None
    if args.poll_interval_ms > 0:
        poll_thread = threading.Thread(
            target=runtime.run_poll_loop,
            args=(args.poll_interval_ms / 1000,),
            daemon=True,
        )
        poll_thread.start()

    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
        event_server.shutdown()
        event_server.server_close()
        coordinator.disconnect()
        if poll_thread is not None:
            poll_thread.join(timeout=2)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Chrome browser collector")
    parser.add_argument(
        "--coordinator-url",
        default=os.environ.get("RADAR_COORDINATOR_URL", "http://127.0.0.1:5000"),
    )
    parser.add_argument(
        "--collector-id",
        default=os.environ.get("RADAR_CHROME_COLLECTOR_ID", CHROME_COLLECTOR_ID),
    )
    parser.add_argument(
        "--event-host",
        default=os.environ.get("RADAR_CHROME_EVENT_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--event-port",
        default=env_int("RADAR_CHROME_EVENT_PORT", 47321),
        type=int,
    )
    parser.add_argument(
        "--poll-interval-ms",
        default=env_int("RADAR_CHROME_POLL_INTERVAL_MS", 2000),
        type=int,
    )
    parser.add_argument(
        "--file-split-ms",
        default=env_int("RADAR_CHROME_FILE_SPLIT_MS", 3_600_000),
        type=int,
    )
    parser.add_argument(
        "--heartbeat-interval",
        default=env_float("RADAR_CHROME_HEARTBEAT_INTERVAL", 5.0),
        type=float,
    )
    parser.add_argument(
        "--connect-timeout",
        default=env_float("RADAR_COLLECTOR_CONNECT_TIMEOUT", 10.0),
        type=float,
    )
    return parser


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    run(args)
