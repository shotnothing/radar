import importlib.util
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_PATH = REPO_ROOT / "builtin" / "collector" / "chat_transcript" / "collector.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("chat_transcript_collector", COLLECTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row))
            output.write("\n")


def build_codex_fixture(root):
    path = root / "2026" / "06" / "06" / "session.jsonl"
    write_jsonl(
        path,
        [
            {
                "timestamp": "2026-06-06T01:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "codex-session-1",
                    "cwd": "/tmp/radar-codex",
                    "originator": "codex_cli",
                    "model_provider": "openai",
                },
            },
            {
                "timestamp": "2026-06-06T01:00:01Z",
                "type": "turn_context",
                "payload": {
                    "cwd": "/tmp/radar-codex",
                    "model": "gpt-test",
                    "effort": "medium",
                },
            },
            {
                "timestamp": "2026-06-06T01:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"text": "Please inspect the collector spec."}],
                },
            },
            {
                "timestamp": "2026-06-06T01:00:03Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call_codex_1",
                    "arguments": "{\"cmd\":\"rg collector\"}",
                },
            },
            {
                "timestamp": "2026-06-06T01:00:04Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_codex_1",
                    "output": {"text": "collector.md"},
                },
            },
        ],
    )
    return path


def build_claude_fixture(root):
    project_dir = root / "-tmp-radar-claude"
    session_id = "claude-session-1"
    transcript = project_dir / "session.jsonl"
    tool_result = project_dir / session_id / "tool-results" / "external_1.txt"
    tool_result.parent.mkdir(parents=True, exist_ok=True)
    tool_result.write_text("external result body", encoding="utf-8")
    write_jsonl(
        transcript,
        [
            {
                "type": "user",
                "uuid": "claude-user-1",
                "sessionId": session_id,
                "timestamp": "2026-06-06T02:00:00Z",
                "cwd": "/tmp/radar-claude",
                "message": {
                    "role": "user",
                    "content": "Read the processor spec too.",
                },
            },
            {
                "type": "assistant",
                "uuid": "claude-assistant-1",
                "sessionId": session_id,
                "timestamp": "2026-06-06T02:00:01Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_claude_1",
                            "name": "Read",
                            "input": {"file_path": "builtin/processor/spec/processor.md"},
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_claude_1",
                            "external": {"id": "external_1"},
                        },
                        {
                            "type": "text",
                            "text": "I read the processor spec.",
                        },
                    ],
                },
            },
        ],
    )
    return transcript, tool_result


def resolve_radar_home():
    configured = os.environ.get("RADAR_HOME", "~/.radar")
    return Path(os.path.expandvars(configured)).expanduser().resolve()


def read_written_events(work_dir, started_at, fixture_root):
    files = sorted(
        path
        for path in Path(work_dir).glob("**/*.jsonl")
        if path.stat().st_mtime >= started_at
    )
    events = []
    fixture_uri_prefix = fixture_root.resolve().as_uri()
    for path in files:
        with path.open(encoding="utf-8") as data_file:
            for line in data_file:
                event = json.loads(line)
                source_uri = (event.get("provenance") or {}).get("source_uri", "")
                if source_uri.startswith(fixture_uri_prefix):
                    events.append(event)
    return events


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    collector = load_collector()
    radar_home = resolve_radar_home()
    fixture_root = radar_home / "test_sources" / "chat_transcript"
    codex_root = fixture_root / "codex" / "sessions"
    claude_root = fixture_root / "claude" / "projects"
    work_dir = radar_home / "collectors" / "chat_transcript"
    codex_path = build_codex_fixture(codex_root)
    claude_path, tool_result_path = build_claude_fixture(claude_root)
    args = SimpleNamespace(
        codex_sessions_root=str(codex_root),
        claude_projects_root=str(claude_root),
        force=True,
        emit_empty_sessions=False,
    )

    started_at = time.time()
    first = collector.scan_sources(args, work_dir)
    events = read_written_events(work_dir, started_at, fixture_root)
    kinds = [event["subject"]["kind"] for event in events]
    require(first["sessions_seen"] == 2, f"unexpected sessions_seen: {first}")
    require(first["sessions_changed"] == 2, f"unexpected sessions_changed: {first}")
    require(kinds.count("chat_session_summary") == 2, f"missing summaries: {kinds}")
    require("chat_message" in kinds, f"missing message event: {kinds}")
    require("chat_tool_call" in kinds, f"missing tool call event: {kinds}")
    require("chat_tool_result" in kinds, f"missing tool result event: {kinds}")

    summaries = [event for event in events if event["subject"]["kind"] == "chat_session_summary"]
    require(
        all(event["artifacts"][0]["storage"] == "external_pointer" for event in summaries),
        "summary raw transcript artifact should be an external pointer",
    )
    require(
        any(event["provenance"]["session_key"].startswith("codex-") for event in summaries),
        "missing codex stable session key",
    )
    require(
        any(event["provenance"]["session_key"].startswith("claude-") for event in summaries),
        "missing claude stable session key",
    )
    require(
        any(
            event["provenance"].get("line_start")
            and event["provenance"].get("source_message_ids")
            for event in events
            if event["subject"]["kind"] == "chat_message"
        ),
        "message events should include line and message provenance",
    )
    require(
        any(
            artifact.get("kind") == "external_tool_result"
            and artifact.get("uri") == tool_result_path.resolve().as_uri()
            for event in events
            for artifact in event.get("artifacts", [])
        ),
        "missing Claude external tool-result pointer artifact",
    )

    checkpoint = json.loads((work_dir / "state" / "checkpoint.json").read_text())
    require(codex_path.resolve().as_uri() in checkpoint["sources"], "codex checkpoint missing")
    require(claude_path.resolve().as_uri() in checkpoint["sources"], "claude checkpoint missing")

    args.force = False
    second = collector.scan_sources(args, work_dir)
    require(second["sessions_changed"] == 0, f"second scan should skip unchanged files: {second}")
    require(second["events_written"] == 0, f"second scan wrote unexpected events: {second}")

    print(f"RADAR_HOME: {radar_home}")
    print(f"fixture_root: {fixture_root}")
    print(f"work_dir: {work_dir}")
    print(f"events_written: {len(events)}")
    print(f"codex_session: {checkpoint['sources'][codex_path.resolve().as_uri()]['session_key']}")
    print(f"claude_session: {checkpoint['sources'][claude_path.resolve().as_uri()]['session_key']}")


if __name__ == "__main__":
    main()
