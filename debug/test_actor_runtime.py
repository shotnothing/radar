from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
