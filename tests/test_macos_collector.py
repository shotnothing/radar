from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from builtin.collector.macos.observation import (
    MACOS_COLLECTOR_ID,
    REDACTED,
    build_observation,
)
from builtin.collector.macos.runtime import MacOSActivityCollectorRuntime


class MacOSObservationTest(unittest.TestCase):
    def test_build_enter_observation_uses_action_trigger_shape(self) -> None:
        observation = build_observation(
            {
                "type": "event",
                "event_name": "enter_key",
                "observed_at": 1780713574000,
                "key_code": 36,
                "context": {
                    "app_name": "Google Chrome",
                    "bundle_id": "com.google.Chrome",
                    "window_title": "ChatGPT",
                    "url": "https://chatgpt.com",
                    "focused_element": {
                        "role": "AXTextArea",
                        "title": "Message ChatGPT",
                        "value": "How do I deploy a Go service?",
                    },
                },
            }
        )

        self.assertEqual(observation["collector_id"], MACOS_COLLECTOR_ID)
        self.assertEqual(observation["source"]["type"], "macos")
        self.assertEqual(observation["source"]["bundle_id"], "com.google.Chrome")
        self.assertEqual(observation["time"]["observed_at"], 1780713574000)
        self.assertEqual(observation["anchor"]["type"], "user_action")
        self.assertEqual(observation["anchor"]["name"], "enter_key")
        self.assertEqual(observation["anchor"]["target"]["element_role"], "AXTextArea")
        self.assertEqual(observation["subject"]["kind"], "window")
        self.assertEqual(observation["subject"]["url"], "https://chatgpt.com")
        self.assertEqual(observation["content"]["text"], "How do I deploy a Go service?")
        self.assertEqual(observation["context"]["user_action"], "submitted")
        self.assertEqual(observation["provenance"]["trigger"], "enter_key")
        self.assertNotIn("collectorId", observation)

    def test_sensitive_focused_value_is_redacted(self) -> None:
        observation = build_observation(
            {
                "type": "event",
                "event_name": "enter_key",
                "context": {
                    "app_name": "Login",
                    "bundle_id": "com.example.Login",
                    "window_title": "Password",
                    "focused_element": {
                        "role": "AXSecureTextField",
                        "title": "password",
                        "value": "super-secret",
                    },
                },
            }
        )

        self.assertEqual(observation["content"]["text"], REDACTED)
        self.assertTrue(observation["privacy"]["redaction_applied"])
        self.assertFalse(observation["privacy"]["contains_raw_content"])
        focused = observation["extra_data"]["macos"]["context"]["focused_element"]
        self.assertEqual(focused["value"], REDACTED)


class MacOSRuntimeTest(unittest.TestCase):
    def test_handle_helper_event_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = MacOSActivityCollectorRuntime(
                work_dir=tmp,
                split_ms=1,
                helper_command=["unused"],
            )

            observation = runtime.handle_helper_line(
                json.dumps(
                    {
                        "type": "event",
                        "event_name": "mouse_click",
                        "observed_at": 1780713574000,
                        "button": "left",
                        "context": {
                            "app_name": "Finder",
                            "bundle_id": "com.apple.finder",
                            "window_title": "Desktop",
                        },
                    }
                )
            )

            self.assertIsNotNone(observation)
            self.assertEqual(runtime.status_payload()["stored_count"], 1)
            last_file = runtime.status_payload()["last_file"]
            self.assertIsInstance(last_file, str)
            line = Path(last_file).read_text(encoding="utf-8").strip()
            stored = json.loads(line)
            self.assertEqual(stored["anchor"]["name"], "mouse_click")
            self.assertEqual(stored["context"]["user_action"], "clicked")

    def test_status_line_updates_permissions_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = MacOSActivityCollectorRuntime(work_dir=tmp, helper_command=["unused"])

            result = runtime.handle_helper_line(
                json.dumps(
                    {
                        "type": "status",
                        "status": "degraded",
                        "permissions": {
                            "macos_accessibility": False,
                            "macos_input_monitoring": "unknown",
                        },
                        "error": "permission missing",
                    }
                )
            )

            status = runtime.status_payload()
            self.assertIsNone(result)
            self.assertEqual(status["stored_count"], 0)
            self.assertEqual(status["permissions"]["macos_accessibility"], "denied")
            self.assertEqual(status["last_error"], "permission missing")
            self.assertEqual(status["helper_status"], "degraded")

    def test_start_reports_helper_setup_error_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = MacOSActivityCollectorRuntime(
                work_dir=tmp,
                helper_command=None,
                compile_helper=True,
            )
            original_build_helper_command = runtime.build_helper_command
            runtime.build_helper_command = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            try:
                runtime.start()
            finally:
                runtime.build_helper_command = original_build_helper_command

            status = runtime.status_payload()
            self.assertEqual(status["stored_count"], 0)
            self.assertIn("could not start macOS helper", status["last_error"])
            self.assertEqual(status["helper_status"], "stopped")


class MacOSMetadataTest(unittest.TestCase):
    def test_meta_uses_managed_collector_command_shape(self) -> None:
        meta_path = Path("builtin/collector/macos/meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        self.assertEqual(meta["collector_id"], MACOS_COLLECTOR_ID)
        self.assertEqual(meta["runtime"]["command"], "python3")
        self.assertEqual(meta["runtime"]["args"], ["-m", "builtin.collector.macos.cli"])
        self.assertIn("macos.accessibility", meta["required_permissions"])
        self.assertIn("enter_key_trigger", meta["capabilities"])


if __name__ == "__main__":
    unittest.main()
