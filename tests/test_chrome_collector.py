from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from builtin.collector.chrome.observation import (
    CHROME_COLLECTOR_ID,
    REDACTED,
    build_observation,
)
from builtin.collector.chrome.runtime import ChromeCollectorRuntime, ChromeTabState
from builtin.collector.chrome.storage import JsonlObservationStore


class FakeChromeStateReader:
    def __init__(self, states: list[ChromeTabState | None]) -> None:
        self.states = states

    def read_active_tab(self) -> ChromeTabState | None:
        if not self.states:
            return None
        return self.states.pop(0)


class ChromeObservationTest(unittest.TestCase):
    def test_build_click_observation(self) -> None:
        observation = build_observation(
            {
                "eventName": "click",
                "observedAt": 1780713574000,
                "occurredAt": "2026-06-06T10:12:04Z",
                "documentTitle": "ChatGPT",
                "documentUrl": "https://chatgpt.com",
                "windowTitle": "ChatGPT",
                "text": "How do I deploy a Go service to Kubernetes?",
                "element": {
                    "tag": "button",
                    "role": "button",
                    "text": "Send",
                    "ariaLabel": "Send",
                },
                "focusedElement": {
                    "tag": "textarea",
                    "role": "textbox",
                    "value": "How do I deploy a Go service to Kubernetes?",
                },
            }
        )

        self.assertEqual(observation["collectorId"], CHROME_COLLECTOR_ID)
        self.assertEqual(observation["source"]["type"], "browser")
        self.assertEqual(observation["time"]["observedAt"], 1780713574000)
        self.assertEqual(observation["anchor"]["name"], "mouse_click")
        self.assertEqual(observation["anchor"]["target"]["elementTitle"], "Send")
        self.assertEqual(observation["subject"]["kind"], "tab")
        self.assertEqual(observation["subject"]["url"], "https://chatgpt.com")
        self.assertEqual(
            observation["content"]["text"],
            "How do I deploy a Go service to Kubernetes?",
        )
        self.assertEqual(observation["context"]["userAction"], "clicked")
        self.assertIn("browser", observation["extraData"])

    def test_password_like_input_is_redacted(self) -> None:
        observation = build_observation(
            {
                "eventName": "input",
                "documentTitle": "Login",
                "documentUrl": "https://example.com/login",
                "element": {
                    "tag": "input",
                    "type": "password",
                    "name": "password",
                },
                "focusedElement": {
                    "tag": "input",
                    "type": "password",
                    "value": "super-secret",
                },
            }
        )

        self.assertEqual(observation["content"]["text"], REDACTED)
        self.assertTrue(observation["privacy"]["redacted"])
        self.assertFalse(observation["privacy"]["rawContentStored"])
        focused = observation["extraData"]["browser"]["focusedElement"]
        self.assertEqual(focused["value"], REDACTED)


class ChromeStorageTest(unittest.TestCase):
    def test_store_writes_jsonl_under_collector_day_artifacts(self) -> None:
        observed_at = int(
            datetime(2026, 6, 6, 10, 12, 4, tzinfo=timezone.utc).timestamp()
            * 1000
        )
        observation = build_observation(
            {
                "eventName": "navigation",
                "observedAt": observed_at,
                "documentTitle": "ChatGPT",
                "documentUrl": "https://chatgpt.com",
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlObservationStore(tmp, split_seconds=1)
            path = store.write(observation)
            expected = (
                Path(tmp)
                / CHROME_COLLECTOR_ID
                / "20260606"
                / "artifacts"
                / f"{observed_at // 1000}.jsonl"
            )

            self.assertEqual(path, expected)
            line = path.read_text(encoding="utf-8").strip()
            self.assertEqual(json.loads(line)["id"], observation["id"])


class ChromeRuntimeTest(unittest.TestCase):
    def test_poll_once_stores_only_changed_active_tab(self) -> None:
        states = [
            ChromeTabState("ChatGPT", "ChatGPT", "https://chatgpt.com"),
            ChromeTabState("ChatGPT", "ChatGPT", "https://chatgpt.com"),
            ChromeTabState("Docs", "Docs", "https://docs.example.com"),
        ]
        reader = FakeChromeStateReader(states)

        with tempfile.TemporaryDirectory() as tmp:
            runtime = ChromeCollectorRuntime(
                storage_root=tmp,
                state_reader=reader,
                split_seconds=1,
            )

            first = runtime.poll_once()
            second = runtime.poll_once()
            third = runtime.poll_once()

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertIsNotNone(third)
            self.assertEqual(runtime.health()["storedCount"], 2)


if __name__ == "__main__":
    unittest.main()
