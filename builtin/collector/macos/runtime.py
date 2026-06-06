"""Runtime for the standalone macOS activity collector."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Any, Callable

from builtin.collector.chrome.coordinator import CoordinatorClient
from builtin.collector.chrome.storage import JsonlObservationStore

from .observation import MACOS_COLLECTOR_ID, build_observation

CAPABILITIES = [
    "active_app",
    "active_window",
    "focused_element",
    "mouse_click_trigger",
    "enter_key_trigger",
    "jsonl_storage",
]

HELPER_SOURCE = Path(__file__).with_name("native") / "RadarMacOSActivityHelper.swift"


class MacOSActivityCollectorRuntime:
    def __init__(
        self,
        *,
        work_dir: str | Path,
        collector_id: str = MACOS_COLLECTOR_ID,
        split_ms: int = 3_600_000,
        helper_command: list[str] | None = None,
        compile_helper: bool = True,
        prompt_permissions: bool = False,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.collector_id = collector_id
        self.store = JsonlObservationStore(work_dir, split_ms=split_ms)
        self.work_dir = Path(work_dir)
        self.helper_command = helper_command
        self.compile_helper = compile_helper
        self.prompt_permissions = prompt_permissions
        self.event_callback = event_callback
        self.lock = threading.Lock()
        self.stored_count = 0
        self.last_observation_at: int | None = None
        self.last_file: str | None = None
        self.last_error: str | None = None
        self.helper_status = "stopped"
        self.helper_pid: int | None = None
        self.permissions: dict[str, str] = {
            "macos_accessibility": "unknown",
            "macos_input_monitoring": "unknown",
        }
        self.paused = False
        self._stop = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            command = self.helper_command or self.build_helper_command()
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            self.set_error(f"could not start macOS helper: {exc}")
            return

        self._process = process
        with self.lock:
            self.helper_status = "running"
            self.helper_pid = process.pid

        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def build_helper_command(self) -> list[str]:
        if platform.system() != "Darwin":
            raise RuntimeError("macOS activity helper is only available on Darwin")
        if not HELPER_SOURCE.exists():
            raise RuntimeError(f"missing helper source: {HELPER_SOURCE}")

        if not self.compile_helper:
            return ["swift", str(HELPER_SOURCE), *self.helper_args()]

        swiftc = shutil.which("swiftc")
        if swiftc is None:
            raise RuntimeError("swiftc is required to compile the macOS activity helper")

        binary = self.work_dir / "state" / "radar_macos_activity_helper"
        source_mtime = int(HELPER_SOURCE.stat().st_mtime_ns)
        stamp = binary.with_suffix(".stamp")
        needs_compile = (
            not binary.exists()
            or not stamp.exists()
            or stamp.read_text(encoding="utf-8", errors="ignore") != str(source_mtime)
        )
        if needs_compile:
            binary.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    swiftc,
                    str(HELPER_SOURCE),
                    "-framework",
                    "AppKit",
                    "-framework",
                    "ApplicationServices",
                    "-framework",
                    "CoreGraphics",
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            stamp.write_text(str(source_mtime), encoding="utf-8")
        return [str(binary), *self.helper_args()]

    def helper_args(self) -> list[str]:
        return ["--prompt-permissions"] if self.prompt_permissions else []

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            if self._stop.is_set():
                break
            self.handle_helper_line(line)
        self._mark_helper_exited()

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            message = line.strip()
            if message:
                self.set_error(message)

    def _mark_helper_exited(self) -> None:
        process = self._process
        exit_code = process.poll() if process is not None else None
        with self.lock:
            if not self._stop.is_set():
                self.helper_status = "exited"
                if exit_code not in (None, 0):
                    self.last_error = f"macOS helper exited with code {exit_code}"

    def handle_helper_line(self, line: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self.set_error(f"invalid helper JSON: {exc}")
            return None

        if not isinstance(payload, dict):
            self.set_error("helper line must be a JSON object")
            return None

        line_type = payload.get("type")
        if line_type == "status":
            self.apply_helper_status(payload)
            return None
        if line_type != "event":
            self.set_error(f"unknown helper line type: {line_type!r}")
            return None

        return self.handle_trigger_event(payload)

    def apply_helper_status(self, payload: dict[str, Any]) -> None:
        permissions = payload.get("permissions")
        with self.lock:
            status = payload.get("status")
            if isinstance(status, str) and status:
                self.helper_status = status
            if isinstance(permissions, dict):
                for key, value in permissions.items():
                    if isinstance(key, str):
                        self.permissions[key] = normalize_permission(value)
            error = payload.get("error")
            if isinstance(error, str) and error:
                self.last_error = error

    def handle_trigger_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if self.paused:
            return None

        observation = build_observation(event, collector_id=self.collector_id)
        path = self.store.write(observation)
        with self.lock:
            self.stored_count += 1
            self.last_observation_at = observation["time"]["observed_at"]
            self.last_file = str(path)
            self.last_error = None
        if self.event_callback is not None:
            self.event_callback(observation)
        return observation

    def status_payload(self) -> dict[str, Any]:
        with self.lock:
            return {
                "status": "paused"
                if self.paused
                else ("degraded" if self.last_error else "ok"),
                "permissions": dict(self.permissions),
                "stored_count": self.stored_count,
                "last_observation_at": self.last_observation_at,
                "last_file": self.last_file,
                "last_error": self.last_error,
                "helper_status": self.helper_status,
                "helper_pid": self.helper_pid,
            }

    def set_error(self, message: str) -> None:
        with self.lock:
            self.last_error = message

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=2)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)
        with self.lock:
            self.helper_status = "stopped"


def normalize_permission(value: Any) -> str:
    if isinstance(value, bool):
        return "granted" if value else "denied"
    if isinstance(value, str) and value:
        return value
    return "unknown"


def run(args: argparse.Namespace) -> None:
    coordinator = CoordinatorClient(
        args.coordinator_url,
        collector_id=args.collector_id,
        capabilities=CAPABILITIES,
        metadata={
            "display_name": "macOS Activity Collector",
        },
    )
    work_dir = coordinator.connect_and_register(
        connect_timeout_seconds=args.connect_timeout,
    )
    runtime = MacOSActivityCollectorRuntime(
        work_dir=work_dir,
        collector_id=args.collector_id,
        split_ms=args.file_split_ms,
        compile_helper=args.compile_helper,
        prompt_permissions=args.prompt_permissions,
        event_callback=make_actor_event_callback(args.coordinator_url),
    )
    runtime.start()
    coordinator.start_heartbeat_loop(runtime.status_payload, args.heartbeat_interval)

    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
        coordinator.disconnect()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the macOS activity collector")
    parser.add_argument(
        "--coordinator-url",
        default=os.environ.get("RADAR_COORDINATOR_URL", "http://127.0.0.1:5000"),
    )
    parser.add_argument(
        "--collector-id",
        default=os.environ.get("RADAR_MACOS_COLLECTOR_ID", MACOS_COLLECTOR_ID),
    )
    parser.add_argument(
        "--file-split-ms",
        default=env_int("RADAR_MACOS_FILE_SPLIT_MS", 3_600_000),
        type=int,
    )
    parser.add_argument(
        "--heartbeat-interval",
        default=env_float("RADAR_MACOS_HEARTBEAT_INTERVAL", 5.0),
        type=float,
    )
    parser.add_argument(
        "--connect-timeout",
        default=env_float("RADAR_COLLECTOR_CONNECT_TIMEOUT", 10.0),
        type=float,
    )
    parser.add_argument(
        "--no-compile-helper",
        action="store_false",
        dest="compile_helper",
        default=env_bool("RADAR_MACOS_COMPILE_HELPER", True),
    )
    parser.add_argument(
        "--prompt-permissions",
        action="store_true",
        default=env_bool("RADAR_MACOS_PROMPT_PERMISSIONS", False),
    )
    return parser


def make_actor_event_callback(coordinator_url: str) -> Callable[[dict[str, Any]], None]:
    url = f"{coordinator_url.rstrip('/')}/debug/actors/evaluate_event"

    def callback(observation: dict[str, Any]) -> None:
        anchor = observation.get("anchor") if isinstance(observation, dict) else {}
        if not isinstance(anchor, dict) or anchor.get("type") != "user_action":
            return
        threading.Thread(
            target=post_actor_event,
            args=(url, observation),
            daemon=True,
        ).start()

    return callback


def post_actor_event(url: str, observation: dict[str, Any]) -> None:
    payload = json.dumps({"event": observation, "run_automatic": True}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2):
            pass
    except Exception:
        pass


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


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    run(args)
