from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from debug.actor_runtime import ActorRuntime

CODEX_SKILL_LIB_PATH = REPO_ROOT / "builtin" / "actor" / "codex_skill" / "lib.py"
spec = importlib.util.spec_from_file_location("codex_skill_actor_lib", CODEX_SKILL_LIB_PATH)
assert spec and spec.loader
codex_skill_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codex_skill_lib)


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


def youtube_background_context(url: str) -> dict[str, object]:
    context = youtube_context(url)
    context["active_context"] = {
        "observed_at": 1780713573900,
        "app_name": "Codex",
        "bundle_id": "com.openai.codex",
        "window_title": "radar",
        "document_path": "/Users/example/radar",
    }
    return context


def gmail_context(url: str) -> dict[str, object]:
    context = youtube_context(url)
    context["active_context"]["window_title"] = "Gmail"
    context["browser"]["active_tab"] = {
        "url": url,
        "title": "Inbox - Gmail",
        "domain": "mail.google.com",
    }
    return context


def calendar_context(url: str) -> dict[str, object]:
    context = youtube_context(url)
    context["active_context"]["window_title"] = "Google Calendar"
    context["browser"]["active_tab"] = {
        "url": url,
        "title": "Google Calendar",
        "domain": "calendar.google.com",
    }
    return context


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
            self.assertIn("builtin.gmail_followup_draft", actor_ids)
            self.assertIn("builtin.gmail_reply_email", actor_ids)
            self.assertIn("builtin.calendar_next_open_timeslot", actor_ids)
            self.assertIn("builtin.codex_use_radar_skill", actor_ids)

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
            self.assertEqual(output["action_context"]["query"], "kpop")
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

    def test_youtube_actor_skips_when_youtube_is_background_tab(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: youtube_background_context("https://www.youtube.com/"),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.youtube_search_nab")

            self.assertFalse(output["available"])

    def test_gmail_actor_should_trigger_on_gmail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: gmail_context("https://mail.google.com/mail/u/0/#inbox"),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.gmail_followup_draft")

            self.assertTrue(output["available"])
            self.assertIn("draft_body", output["action_context"])
            self.assertIn("action_request", output)

    def test_gmail_actor_filter_skips_non_gmail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: gmail_context("https://calendar.google.com/calendar/u/0/r"),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.gmail_followup_draft")

            self.assertFalse(output["available"])
            self.assertTrue(output["filtered"])

    def test_gmail_reply_actor_should_trigger_on_open_email_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: gmail_context(
                    "https://mail.google.com/mail/u/0/#inbox/FMfcgzQgMLvxdbztfPfkNcTpBjgjhKDr"
                ),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.gmail_reply_email")

            self.assertTrue(output["available"])
            self.assertEqual(output["presentation"]["button_label"], "reply this email")
            self.assertIn("action_request", output)

    def test_gmail_reply_actor_skips_gmail_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: gmail_context("https://mail.google.com/mail/u/0/#inbox"),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.gmail_reply_email")

            self.assertFalse(output["available"])

    def test_gmail_reply_actor_action_invokes_codex_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_codex = Path(tmp) / "codex"
            captured_args = Path(tmp) / "args.json"
            log_path = Path(tmp) / "gmail_reply_action.log"
            fake_codex.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, pathlib, sys",
                        f"pathlib.Path({str(captured_args)!r}).write_text(json.dumps(sys.argv[1:]))",
                        "print('reply editor opened')",
                    ]
                )
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            payload = {
                "action_context": {
                    "url": "https://mail.google.com/mail/u/0/#inbox/FMfcgzQgMLvxdbztfPfkNcTpBjgjhKDr"
                }
            }
            env = os.environ.copy()
            env["RADAR_CODEX_BIN"] = str(fake_codex)
            env["RADAR_GMAIL_REPLY_ACTION_LOG"] = str(log_path)
            result = subprocess.run(
                ["python3", "builtin/actor/gmail_reply_email/action.py"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            args = json.loads(captured_args.read_text())

            self.assertTrue(output["success"])
            self.assertEqual(args[0], "exec")
            self.assertIn("--ask-for-approval", args)
            self.assertIn("never", args)
            self.assertIn("--sandbox", args)
            self.assertIn("danger-full-access", args)
            self.assertIn("Do not type a reply body and do not click Send.", args[-1])
            self.assertIn(payload["action_context"]["url"], args[-1])

            log_entries = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([entry["event"] for entry in log_entries], ["triggered", "completed"])
            self.assertEqual(log_entries[0]["url"], payload["action_context"]["url"])
            self.assertTrue(log_entries[1]["success"])
            self.assertEqual(log_entries[1]["returncode"], 0)

    def test_calendar_actor_should_trigger_on_calendar_week_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: calendar_context(
                    "https://calendar.google.com/calendar/u/0/r/week"
                ),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.calendar_next_open_timeslot")

            self.assertTrue(output["available"])
            self.assertEqual(
                output["presentation"]["button_label"],
                "Find my next open timeslot",
            )
            self.assertIn("action_request", output)

    def test_calendar_actor_filter_skips_non_calendar_week_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = ActorRuntime(
                actor_paths=["builtin/actor"],
                state_root=tmp,
                context_provider=lambda: calendar_context(
                    "https://calendar.google.com/calendar/u/0/r/day"
                ),
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            output = runtime.should_trigger("builtin.calendar_next_open_timeslot")

            self.assertFalse(output["available"])
            self.assertTrue(output["filtered"])

    def test_calendar_actor_action_invokes_codex_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_codex = Path(tmp) / "codex"
            captured_args = Path(tmp) / "args.json"
            log_path = Path(tmp) / "calendar_action.log"
            fake_codex.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, pathlib, sys",
                        f"pathlib.Path({str(captured_args)!r}).write_text(json.dumps(sys.argv[1:]))",
                        "args = sys.argv[1:]",
                        "out = args[args.index('--output-last-message') + 1]",
                        "pathlib.Path(out).write_text('Monday, June 8, 2026, 10:00-10:30 AM SGT. Evidence: visible week view has no event in that slot.')",
                    ]
                )
            )
            fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)

            payload = {
                "action_context": {
                    "url": "https://calendar.google.com/calendar/u/0/r/week",
                },
                "browser": {
                    "active_tab": {
                        "url": "https://calendar.google.com/calendar/u/0/r/week",
                        "title": "Google Calendar",
                    }
                },
            }
            env = os.environ.copy()
            env["RADAR_CODEX_BIN"] = str(fake_codex)
            env["RADAR_API_URL"] = ""
            env["RADAR_CALENDAR_ACTION_LOG"] = str(log_path)
            result = subprocess.run(
                ["python3", "builtin/actor/calendar_next_open_timeslot/action.py"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            args = json.loads(captured_args.read_text())

            self.assertTrue(output["success"])
            self.assertIn("10:00-10:30 AM SGT", output["answer"])
            self.assertEqual(args[0], "exec")
            self.assertIn("--ask-for-approval", args)
            self.assertIn("never", args)
            self.assertIn("--sandbox", args)
            self.assertIn("danger-full-access", args)
            self.assertIn("--output-last-message", args)
            self.assertIn("find my next open timeslot", args[-1])
            self.assertIn("Do not create, edit, delete", args[-1])
            self.assertIn(payload["action_context"]["url"], args[-1])

            log_entries = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([entry["event"] for entry in log_entries], ["triggered", "completed"])
            self.assertEqual(log_entries[0]["url"], payload["action_context"]["url"])
            self.assertTrue(log_entries[1]["success"])
            self.assertEqual(log_entries[1]["exit_code"], 0)

    def test_event_name_filter_requires_matching_trigger_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actor_dir = root / "actors" / "click_actor"
            actor_dir.mkdir(parents=True)
            (actor_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "actor_id": "test.click_actor",
                        "enabled": True,
                        "activation": {"mode": "manual"},
                        "trigger": {
                            "filters": {
                                "event_names": ["mouse_click"],
                                "app_patterns": ["SeaTalk"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            runtime = ActorRuntime(
                actor_paths=[root / "actors"],
                state_root=root / "state",
                context_provider=lambda: {
                    "active_context": {
                        "app_name": "SeaTalk",
                        "bundle_id": "com.seagroup.seatalkmac.enterprise",
                    }
                },
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            polled = runtime.should_trigger("test.click_actor")
            clicked = runtime.should_trigger(
                "test.click_actor",
                trigger_event={"anchor": {"name": "mouse_click", "type": "user_action"}},
            )

            self.assertFalse(polled["available"])
            self.assertTrue(polled["filtered"])
            self.assertTrue(clicked["available"])

    def test_evaluate_event_skips_poll_only_actors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actors = root / "actors"
            poll_actor = actors / "poll_actor"
            event_actor = actors / "event_actor"
            poll_actor.mkdir(parents=True)
            event_actor.mkdir(parents=True)
            (poll_actor / "manifest.json").write_text(
                json.dumps(
                    {
                        "actor_id": "test.poll_actor",
                        "enabled": True,
                        "activation": {"mode": "manual"},
                        "trigger": {"filters": {}},
                    }
                ),
                encoding="utf-8",
            )
            (event_actor / "manifest.json").write_text(
                json.dumps(
                    {
                        "actor_id": "test.event_actor",
                        "enabled": True,
                        "activation": {"mode": "manual"},
                        "trigger": {"filters": {"event_names": ["mouse_click"]}},
                    }
                ),
                encoding="utf-8",
            )
            runtime = ActorRuntime(
                actor_paths=[actors],
                state_root=root / "state",
                context_provider=lambda: {"active_context": {"app_name": "SeaTalk"}},
                api_url="http://127.0.0.1:5000",
                api_token="test-token",
            )
            runtime.refresh()

            outputs = runtime.evaluate_event({"anchor": {"name": "mouse_click"}})

            self.assertEqual([item["actor_id"] for item in outputs], ["test.event_actor"])

    def test_codex_skill_actor_finds_repo_skill_from_axtree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "workspace" / "radar"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            repo = repo.resolve()

            skill_dir = (root / "skill").resolve()
            repo_skill = codex_skill_lib.project_skill_path(skill_dir, repo)
            repo_skill.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Radar Coding Memory\n", encoding="utf-8")
            (repo_skill / "common_workflows.md").write_text("# Common Workflows\n", encoding="utf-8")

            payload = {
                "active_context": {
                    "app_name": "Codex",
                    "window_title": "radar",
                }
            }
            accessibility = {
                "success": True,
                "data": {
                    "app_name": "Codex",
                    "tree": {
                        "role": "AXWindow",
                        "title": "Codex",
                        "value": f"Working in {repo}",
                        "children": [],
                    },
                },
            }

            result = codex_skill_lib.find_available_skill(payload, accessibility, skill_dir)

            self.assertTrue(result["available"])
            self.assertEqual(result["repo_path"], str(repo))
            self.assertEqual(result["skill_path"], str(skill_dir.resolve()))
            self.assertEqual(result["repo_skill_path"], str(repo_skill))

    def test_codex_skill_actor_skips_without_repo_specific_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "workspace" / "radar"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            repo = repo.resolve()

            skill_dir = root / "skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("# Radar Coding Memory\n", encoding="utf-8")

            payload = {
                "active_context": {
                    "app_name": "Codex",
                    "window_title": str(repo),
                }
            }

            result = codex_skill_lib.find_available_skill(payload, None, skill_dir)

            self.assertFalse(result["available"])
            self.assertIn("No Radar skill entries", result["reason"])

    def test_codex_skill_actor_falls_back_to_recent_codex_session_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "workspace" / "radar"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            repo = repo.resolve()

            codex_home = root / "codex"
            session_file = codex_home / "sessions" / "2026" / "06" / "06" / "session.jsonl"
            session_file.parent.mkdir(parents=True)
            session_file.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "cwd": str(repo),
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            skill_dir = (root / "skill").resolve()
            repo_skill = codex_skill_lib.project_skill_path(skill_dir, repo)
            repo_skill.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Radar Coding Memory\n", encoding="utf-8")
            (repo_skill / "common_workflows.md").write_text("# Common Workflows\n", encoding="utf-8")

            previous_codex_home = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = str(codex_home)
            try:
                result = codex_skill_lib.find_available_skill(
                    {
                        "active_context": {
                            "app_name": "Codex",
                            "window_title": "Codex",
                        }
                    },
                    {"success": True, "data": {"app_name": "Codex", "tree": {}}},
                    skill_dir,
                )
            finally:
                if previous_codex_home is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = previous_codex_home

            self.assertTrue(result["available"])
            self.assertEqual(result["repo_path"], str(repo))
            self.assertEqual(result["repo_source"], "recent_codex_session")


if __name__ == "__main__":
    unittest.main()
