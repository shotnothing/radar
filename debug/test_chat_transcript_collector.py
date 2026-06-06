import importlib.util
import json
import tempfile
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


def read_written_events(work_dir):
    files = sorted(Path(work_dir).glob("**/*.jsonl"))
    events = []
    for path in files:
        with path.open(encoding="utf-8") as data_file:
            for line in data_file:
                events.append(json.loads(line))
    return events


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    collector = load_collector()
    with tempfile.TemporaryDirectory(prefix="radar-chat-collector-test-") as tmp:
        tmp = Path(tmp)
        codex_root = tmp / "codex" / "sessions"
        claude_root = tmp / "claude" / "projects"
        work_dir = tmp / "radar" / "collectors" / "chat_transcript"
        codex_path = build_codex_fixture(codex_root)
        claude_path, tool_result_path = build_claude_fixture(claude_root)
        args = SimpleNamespace(
            codex_sessions_root=str(codex_root),
            claude_projects_root=str(claude_root),
            force=False,
            emit_empty_sessions=False,
        )

        first = collector.scan_sources(args, work_dir)
        events = read_written_events(work_dir)
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

        second = collector.scan_sources(args, work_dir)
        require(second["sessions_changed"] == 0, f"second scan should skip unchanged files: {second}")
        require(second["events_written"] == 0, f"second scan wrote unexpected events: {second}")

        print(f"work_dir: {work_dir}")
        print(f"events_written: {len(events)}")
        print(f"codex_session: {checkpoint['sources'][codex_path.resolve().as_uri()]['session_key']}")
        print(f"claude_session: {checkpoint['sources'][claude_path.resolve().as_uri()]['session_key']}")


if __name__ == "__main__":
    main()
