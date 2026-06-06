from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from debug.actor_runtime import ActorRuntime


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


class ActorRuntimeTest(unittest.TestCase):
    def test_discovers_builtin_youtube_actor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: youtube_context("https://www.youtube.com/"),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            actor_ids = {actor["actor_id"] for actor in runtime.list_actors()}

            self.assertIn("builtin.youtube_search_nab", actor_ids)

    def test_youtube_actor_should_trigger_on_youtube(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: youtube_context("https://www.youtube.com/"),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.youtube_search_nab")

            self.assertTrue(output["available"])
            self.assertEqual(output["action_context"]["query"], "NAB")
            self.assertIn("action_request", output)

    def test_youtube_actor_filter_skips_non_youtube(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: youtube_context("https://example.com/"),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.youtube_search_nab")

            self.assertFalse(output["available"])
            self.assertTrue(output["filtered"])


if __name__ == "__main__":
    unittest.main()
