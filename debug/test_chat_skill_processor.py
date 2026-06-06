import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from debug.test_chat_transcript_collector import (
    build_claude_fixture,
    build_codex_fixture,
)


COLLECTOR_PATH = REPO_ROOT / "builtin" / "collector" / "chat_transcript" / "collector.py"
PROCESSOR_PATH = REPO_ROOT / "builtin" / "processor" / "chat_skill" / "processor.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    with tempfile.TemporaryDirectory(prefix="radar-chat-skill-test-") as temp_dir:
        radar_home = Path(temp_dir).resolve()
        fixture_root = radar_home / "test_sources" / "chat_skill_processor"
        collectors_root = radar_home / "collectors"
        collector_work_dir = collectors_root / "chat_transcript"
        skill_dir = radar_home / "skill"
        processor_state = radar_home / "processors" / "chat_skill" / "state.json"

        collector = load_module("chat_transcript_collector", COLLECTOR_PATH)
        processor = load_module("chat_skill_processor", PROCESSOR_PATH)

        codex_root = fixture_root / "codex" / "sessions"
        claude_root = fixture_root / "claude" / "projects"
        build_codex_fixture(codex_root)
        build_claude_fixture(claude_root)

        collector_args = SimpleNamespace(
            codex_sessions_root=str(codex_root),
            claude_projects_root=str(claude_root),
            force=True,
            emit_empty_sessions=False,
        )
        collector_result = collector.scan_sources(collector_args, collector_work_dir)
        require(
            collector_result["sessions_changed"] == 2,
            f"collector did not emit fixtures: {collector_result}",
        )

        processor_args = SimpleNamespace(
            radar_home=radar_home,
            skill_dir=skill_dir,
            collectors_root=collectors_root,
            force=True,
            filter_llm_api_key="",
            filter_llm_base_url="https://api.openai.com/v1",
            filter_llm_model="gpt-4.1-mini",
            filter_llm_timeout=1,
            extraction_llm_api_key="",
            extraction_llm_base_url="https://api.openai.com/v1",
            extraction_llm_model="gpt-4.1",
            extraction_llm_timeout=1,
            filter_prompt=processor.DEFAULT_FILTER_PROMPT,
            extraction_prompt=processor.DEFAULT_EXTRACTION_PROMPT,
        )
        results = processor.process_collectors_root(processor_args)
        extracted = [result for result in results if result["payload"]["status"] == "extracted"]
        require(
            extracted,
            f"processor did not extract any skill entries: {json.dumps(results, indent=2)}",
        )
        require((skill_dir / "SKILL.md").exists(), "skill index missing")
        require(
            (skill_dir / "references" / "extraction-playbook.md").exists(),
            "skill references missing",
        )
        changed_files = [
            Path(path)
            for result in extracted
            for path in result["payload"].get("changed_files", [])
        ]
        require(changed_files, "no changed skill files reported")
        require(
            any(
                path.exists() and "Source Evidence" in path.read_text(encoding="utf-8")
                for path in changed_files
            ),
            "skill entry missing source evidence",
        )
        require(processor_state.exists(), "processor checkpoint missing")
        state = json.loads(processor_state.read_text(encoding="utf-8"))
        require(state.get("processed"), "processor state should record processed sessions")

        processor_args.force = False
        second = processor.process_collectors_root(processor_args)
        require(not second, f"second processor run should skip unchanged sessions: {second}")

        print(f"RADAR_HOME: {radar_home}")
        print(f"collectors_root: {collectors_root}")
        print(f"skill_dir: {skill_dir}")
        print(f"extracted_results: {len(extracted)}")
        print(f"changed_files: {len(changed_files)}")


if __name__ == "__main__":
    main()
