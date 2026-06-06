"""Standalone Chrome collector runtime."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request

from .observation import CHROME_COLLECTOR_ID, build_observation, epoch_ms_now
from .storage import JsonlObservationStore


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


class EngineClient:
    """Optional HTTP push client for the engine ingest endpoint."""

    def __init__(self, push_url: str | None, timeout_seconds: float = 2.0) -> None:
        self.push_url = push_url
        self.timeout_seconds = timeout_seconds

    def push(self, observation: dict[str, Any]) -> bool:
        if not self.push_url:
            return False

        payload = json.dumps(observation).encode("utf-8")
        req = request.Request(
            self.push_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return 200 <= response.status < 300
        except OSError:
            return False


class ChromeCollectorRuntime:
    def __init__(
        self,
        *,
        storage_root: str | Path,
        collector_id: str = CHROME_COLLECTOR_ID,
        split_seconds: int = 3600,
        engine_push_url: str | None = None,
        state_reader: ChromeStateReader | None = None,
    ) -> None:
        self.collector_id = collector_id
        self.store = JsonlObservationStore(
            storage_root,
            collector_id=collector_id,
            split_seconds=split_seconds,
        )
        self.engine = EngineClient(engine_push_url)
        self.state_reader = state_reader or ChromeStateReader()
        self.lock = threading.Lock()
        self.stored_count = 0
        self.pushed_count = 0
        self.last_observation_at: int | None = None
        self.last_error: str | None = None
        self._last_tab_key: tuple[str, str] | None = None
        self._stop = threading.Event()

    def handle_event(self, event: dict[str, Any]) -> dict[str, Any]:
        observation = build_observation(event, collector_id=self.collector_id)
        self.store.write(observation)
        pushed = self.engine.push(observation)
        with self.lock:
            self.stored_count += 1
            if pushed:
                self.pushed_count += 1
            self.last_observation_at = observation["time"]["observedAt"]
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
        event = {
            "eventName": "navigation",
            "observedAt": epoch_ms_now(),
            "documentTitle": state.title,
            "documentUrl": state.url,
            "windowTitle": state.window_title,
        }
        return self.handle_event(event)

    def health(self) -> dict[str, Any]:
        with self.lock:
            status = "healthy" if self.last_error is None else "degraded"
            return {
                "collectorId": self.collector_id,
                "status": status,
                "storedCount": self.stored_count,
                "pushedCount": self.pushed_count,
                "lastObservationAt": self.last_observation_at,
                "lastError": self.last_error,
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
    class ChromeCollectorHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_json(200, runtime.health())
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
                self.send_json(400, {"error": "request body must be an object or array of objects"})
                return

            observations = [runtime.handle_event(event) for event in events]
            self.send_json(200, {"observations": observations})

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

    return ChromeCollectorHandler


def run_server(args: argparse.Namespace) -> None:
    runtime = ChromeCollectorRuntime(
        storage_root=args.storage_root,
        collector_id=args.collector_id,
        split_seconds=args.split_seconds,
        engine_push_url=args.engine_push_url,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(runtime))
    poll_thread: threading.Thread | None = None
    if args.poll_interval > 0:
        poll_thread = threading.Thread(
            target=runtime.run_poll_loop,
            args=(args.poll_interval,),
            daemon=True,
        )
        poll_thread.start()

    try:
        server.serve_forever()
    finally:
        runtime.stop()
        server.server_close()
        if poll_thread is not None:
            poll_thread.join(timeout=2)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Chrome collector")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47321)
    parser.add_argument("--collector-id", default=CHROME_COLLECTOR_ID)
    parser.add_argument("--storage-root", default="data/collectors")
    parser.add_argument("--split-seconds", type=int, default=3600)
    parser.add_argument("--engine-push-url", default=None)
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between active-tab polls. Use 0 to disable polling.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    run_server(args)
