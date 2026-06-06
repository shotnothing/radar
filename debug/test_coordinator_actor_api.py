from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHROME_BRIDGE_PORT = int(os.environ.get("RADAR_CHROME_BRIDGE_PORT", "9223"))
RADAR_EXTENSION_DIR = REPO_ROOT / "dist" / "radar-extension"
YOUTUBE_ACTOR_ID = "builtin.youtube_search_nab"
YOUTUBE_SEARCH_SELECTOR = ".yt-searchbox-input"


class CoordinatorActorAPILiveTest(unittest.TestCase):
    def test_youtube_actor_runs_through_real_chrome_extension(self) -> None:
        port = free_port()
        token = "test-token"
        radar_home = resolve_radar_home()

        env = os.environ.copy()
        env["RADAR_API_TOKEN"] = token
        env["RADAR_HOME"] = str(radar_home)
        process = subprocess.Popen(
            [
                sys.executable,
                "debug/app.py",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--work-dir",
                str(radar_home),
                "--chrome-bridge-port",
                str(CHROME_BRIDGE_PORT),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            wait_for_http(f"{base_url}/api/actors")
            wait_for_chrome_bridge(base_url)

            page = post_json(
                f"{base_url}/api/browser/page_content",
                {
                    "include_metadata": True,
                },
                token,
            )
            active_url = str(page.get("url") or "")
            if "youtube.com" not in active_url:
                raise AssertionError(
                    "Live actor test requires Google Chrome's active tab to be "
                    f"https://www.youtube.com/. Current active tab: {active_url!r}"
                    f"; page_content response: {page!r}"
                )

            trigger = post_json(
                f"{base_url}/api/actors/{YOUTUBE_ACTOR_ID}/should_trigger",
                {},
                token,
            )
            self.assertTrue(trigger["ok"])
            self.assertTrue(trigger["output"]["available"], trigger["output"])
            trigger_id = trigger["output"]["trigger_id"]

            result = post_json(
                f"{base_url}/api/actors/{YOUTUBE_ACTOR_ID}/run",
                {
                    "trigger_id": trigger_id,
                },
                token,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["output"]["status"], "completed", result["output"])

            filled = post_json(
                f"{base_url}/api/browser/page_content",
                {
                    "selectors": [
                        {
                            "name": "search_value",
                            "selector": YOUTUBE_SEARCH_SELECTOR,
                            "property": "value",
                        }
                    ],
                },
                token,
            )
            self.assertTrue(filled["success"], filled)
            selectors = filled.get("selectors") or {}
            self.assertEqual(selectors.get("search_value"), "kpop", filled)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def resolve_radar_home() -> Path:
    configured = os.environ.get("RADAR_HOME", "~/.radar")
    return Path(os.path.expandvars(configured)).expanduser().resolve()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            get_json(url)
            return
        except Exception as error:
            last_error = error
            time.sleep(0.2)
    raise RuntimeError(f"server did not become ready: {last_error}")


def wait_for_chrome_bridge(base_url: str, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_status: dict[str, object] | None = None
    while time.monotonic() < deadline:
        last_status = get_json(f"{base_url}/api/chrome_bridge/status")
        if last_status.get("connected"):
            return
        time.sleep(0.5)
    raise AssertionError(
        "Chrome bridge extension did not connect.\n"
        "Run `make actor-live-setup` for setup steps.\n"
        "Expected setup: package Radar's Chrome extension, load this "
        "unpacked extension folder in Chrome, and open YouTube:\n"
        f"  {RADAR_EXTENSION_DIR}\n"
        f"Expected bridge URL: ws://127.0.0.1:{CHROME_BRIDGE_PORT}"
        "/radar-chrome-bridge-ws\n"
        f"Last bridge status: {last_status}"
    )


def get_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, object], token: str) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise AssertionError(f"HTTP {error.code}: {body}") from error


if __name__ == "__main__":
    unittest.main()
