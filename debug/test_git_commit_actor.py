from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
import os
import time
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_PATH = REPO_ROOT / "builtin" / "actor" / "git_commit_message" / "lib.py"
spec = importlib.util.spec_from_file_location("git_commit_message_lib", LIB_PATH)
assert spec and spec.loader
git_commit_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(git_commit_lib)


def git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class GitCommitActorTest(unittest.TestCase):
    def test_detects_supported_git_commit_command(self) -> None:
        command = git_commit_lib.detect_git_commit_command(
            {
                "app_name": "Terminal",
                "signals": {
                    "focused_value": "radar % git commit -m ",
                },
            }
        )

        self.assertIsNotNone(command)
        self.assertEqual(command["command_tail"], " -m ")
        self.assertTrue(git_commit_lib.command_tail_is_supported(command["command_tail"]))

    def test_rejects_existing_commit_message_argument(self) -> None:
        command = git_commit_lib.detect_git_commit_command(
            {
                "app_name": "Terminal",
                "signals": {
                    "focused_value": 'radar % git commit -m "manual message"',
                },
            }
        )

        self.assertIsNotNone(command)
        self.assertFalse(git_commit_lib.command_tail_is_supported(command["command_tail"]))

    def test_detects_git_commit_from_recent_key_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            radar_home = Path(tmp)
            log_path = radar_home / "collectors" / "macos_activity" / "20260606" / "events.jsonl"
            log_path.parent.mkdir(parents=True)
            now_ms = int(time.time() * 1000)
            rows = []
            for index, char in enumerate("git commit -m "):
                rows.append(
                    {
                        "time": {"observed_at": now_ms + index},
                        "anchor": {"name": "key_input"},
                        "extra_data": {
                            "macos": {
                                "event_name": "key_input",
                                "key_code": 0,
                                "text": char,
                            }
                        },
                    }
                )
            log_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            previous_radar_home = os.environ.get("RADAR_HOME")
            os.environ["RADAR_HOME"] = str(radar_home)
            try:
                command = git_commit_lib.detect_git_commit_command(
                    {
                        "app_name": "Cursor",
                        "bundle_id": "com.todesktop.230313mzl4w4u92",
                        "signals": {},
                    }
                )
            finally:
                if previous_radar_home is None:
                    os.environ.pop("RADAR_HOME", None)
                else:
                    os.environ["RADAR_HOME"] = previous_radar_home

            self.assertIsNotNone(command)
            self.assertEqual(command["command_tail"], " -m ")

    def test_reads_git_diff_and_generates_fallback_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(["init"], repo)
            git(["config", "user.email", "radar@example.com"], repo)
            git(["config", "user.name", "Radar"], repo)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            git(["add", "README.md"], repo)
            git(["commit", "-m", "chore: initial"], repo)

            (repo / "README.md").write_text("hello\nworld\n", encoding="utf-8")

            diff = git_commit_lib.git_diff_for_commit(str(repo))
            message = git_commit_lib.heuristic_commit_message(diff)

            self.assertTrue(diff["diff"])
            self.assertEqual(diff["file_count"], 1)
            self.assertIn("README.md", diff["files"])
            self.assertTrue(message.startswith("docs") or message.startswith("fix"))


if __name__ == "__main__":
    unittest.main()
