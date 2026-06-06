from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from builtin.collector.chrome.coordinator import CoordinatorClient
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


class FakeSocketClient:
    def __init__(self, ack: dict[str, object]) -> None:
        self.ack = ack
        self.connected_to: str | None = None
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.handlers: dict[str, object] = {}
        self.disconnected = False

    def on(self, event: str):
        def decorator(func):
            self.handlers[event] = func
            return func

        return decorator

    def connect(self, url: str) -> None:
        self.connected_to = url

    def call(self, event: str, payload: dict[str, object], timeout: float = 0) -> dict[str, object]:
        self.calls.append((event, payload))
        if event == "collector:register":
            return self.ack
        return {"ok": True}

    def disconnect(self) -> None:
        self.disconnected = True


class ChromeObservationTest(unittest.TestCase):
    def test_build_click_observation_uses_snake_case_spec_shape(self) -> None:
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

        self.assertEqual(observation["collector_id"], CHROME_COLLECTOR_ID)
        self.assertEqual(observation["source"]["type"], "browser")
        self.assertEqual(observation["source"]["bundle_id"], "com.google.Chrome")
        self.assertEqual(observation["time"]["observed_at"], 1780713574000)
        self.assertEqual(observation["anchor"]["name"], "mouse_click")
        self.assertEqual(observation["anchor"]["target"]["element_title"], "Send")
        self.assertEqual(observation["subject"]["kind"], "tab")
        self.assertEqual(observation["subject"]["url"], "https://chatgpt.com")
        self.assertEqual(
            observation["content"]["text"],
            "How do I deploy a Go service to Kubernetes?",
        )
        self.assertEqual(observation["context"]["user_action"], "clicked")
        self.assertEqual(
            observation["extra_data"]["browser"]["document_url"],
            "https://chatgpt.com",
        )
        self.assertNotIn("collectorId", observation)

    def test_sensitive_input_is_redacted(self) -> None:
        observation = build_observation(
            {
                "event_name": "input",
                "document_title": "Login",
                "document_url": "https://example.com/login",
                "element": {
                    "tag": "input",
                    "type": "password",
                    "name": "password",
                },
                "focused_element": {
                    "tag": "input",
                    "type": "password",
                    "value": "super-secret",
                },
            }
        )

        self.assertEqual(observation["content"]["text"], REDACTED)
        self.assertTrue(observation["privacy"]["redaction_applied"])
        self.assertFalse(observation["privacy"]["contains_raw_content"])
        focused = observation["extra_data"]["browser"]["focused_element"]
        self.assertEqual(focused["value"], REDACTED)


class ChromeStorageTest(unittest.TestCase):
    def test_store_writes_jsonl_under_work_dir_day_artifacts(self) -> None:
        observed_at = int(datetime(2026, 6, 6, 10, 12, 4).timestamp() * 1000)
        observation = build_observation(
            {
                "event_name": "navigation",
                "observed_at": observed_at,
                "document_title": "ChatGPT",
                "document_url": "https://chatgpt.com",
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlObservationStore(tmp, split_ms=1)
            path = store.write(observation)
            expected = Path(tmp) / "20260606" / "artifacts" / f"{observed_at}.jsonl"

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
                work_dir=tmp,
                state_reader=reader,
                split_ms=1,
            )

            first = runtime.poll_once()
            second = runtime.poll_once()
            third = runtime.poll_once()

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertIsNotNone(third)
            self.assertEqual(runtime.status_payload()["stored_count"], 2)


class CoordinatorClientTest(unittest.TestCase):
    def test_register_sends_collector_register_and_returns_work_dir(self) -> None:
        socket = FakeSocketClient(
            {
                "ok": True,
                "role": "collector",
                "collector_id": CHROME_COLLECTOR_ID,
                "work_dir": "/tmp/radar/chrome",
            }
        )
        client = CoordinatorClient(
            "http://127.0.0.1:5000",
            collector_id=CHROME_COLLECTOR_ID,
            capabilities=["active_tab"],
            metadata={"display_name": "Chrome Browser Collector"},
            socket_client=socket,
        )

        work_dir = client.connect_and_register()

        self.assertEqual(work_dir, "/tmp/radar/chrome")
        self.assertEqual(socket.connected_to, "http://127.0.0.1:5000")
        event, payload = socket.calls[0]
        self.assertEqual(event, "collector:register")
        self.assertEqual(payload["collector_id"], CHROME_COLLECTOR_ID)
        self.assertEqual(payload["protocol_version"], 1)


if __name__ == "__main__":
    unittest.main()
