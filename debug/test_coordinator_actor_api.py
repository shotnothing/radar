from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def youtube_context(url: str) -> dict[str, object]:
    return {
        "timestamp": 1780713574000,
        "active_context": {
            "observed_at": 1780713573900,
            "app_name": "Google Chrome",
            "bundle_id": "com.google.Chrome",
            "window_title": "YouTube",
            "document_path": url,
            "signals": {"url": url},
        },
        "browser": {
            "connected": True,
            "active_tab": {
                "url": url,
                "title": "YouTube",
                "domain": "www.youtube.com",
            },
        },
    }


class CoordinatorActorAPITest(unittest.TestCase):
    def test_actor_api_should_trigger_youtube_actor(self) -> None:
        port = free_port()
        token = "test-token"

        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["RADAR_API_TOKEN"] = token
            process = subprocess.Popen(
                [
                    sys.executable,
                    "debug/app.py",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--work-dir",
                    tmp,
                    "--disable-chrome-bridge",
                ],
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                wait_for_http(f"http://127.0.0.1:{port}/api/actors")
                actors = get_json(f"http://127.0.0.1:{port}/api/actors")
                actor_ids = {actor["actor_id"] for actor in actors["actors"]}
                self.assertIn("builtin.youtube_search_nab", actor_ids)

                result = post_json(
                    f"http://127.0.0.1:{port}/api/actors/builtin.youtube_search_nab/should_trigger",
                    {
                        "context": youtube_context("https://www.youtube.com/"),
                    },
                    token,
                )

                self.assertTrue(result["ok"])
                self.assertTrue(result["output"]["available"])
                self.assertEqual(result["output"]["action_context"]["query"], "NAB")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


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
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise AssertionError(f"HTTP {error.code}: {body}") from error


if __name__ == "__main__":
    unittest.main()
