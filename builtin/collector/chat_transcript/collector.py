import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import socketio


COLLECTOR_ID = "chat.transcript"
DISPLAY_NAME = "Chat Transcript Collector"


def observed_time_ms():
    return int(time.time() * 1000)


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def short_hash(value, length=12):
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:length]


def source_uri(path):
    return Path(path).expanduser().resolve().as_uri()


def source_fingerprint(path):
    stat = Path(path).stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def source_updated_at_ms(path):
    return int(Path(path).stat().st_mtime * 1000)


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as data_file:
        for line_no, line in enumerate(data_file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError:
                continue


def text_from_content(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "\n\n".join(parts)
    return ""


def any_from_json_string(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def truncate_text(value, limit=240):
    value = " ".join(str(value or "").split())
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit].strip() + "..."


def iso_to_ms(value):
    if not value:
        return 0
    try:
        return int(
            datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
            * 1000
        )
    except ValueError:
        return 0


def file_artifact(artifact_id, kind, path, fingerprint=None):
    artifact = {
        "id": artifact_id,
        "kind": kind,
        "uri": source_uri(path),
        "storage": "external_pointer",
        "mime_type": "application/x-jsonlines"
        if str(path).endswith(".jsonl")
        else "text/plain",
    }
    try:
        artifact["size_bytes"] = Path(path).stat().st_size
    except OSError:
        pass
    if fingerprint:
        artifact["extra_data"] = {"source_fingerprint": fingerprint}
    return artifact


def base_event(
    collector_id,
    source_app,
    source_type,
    subject_kind,
    anchor_type,
    anchor_id,
    observed_at,
    title,
    content_text,
    provenance,
    artifacts=None,
    extra_chat=None,
):
    return {
        "id": str(uuid.uuid4()),
        "collector_id": collector_id,
        "source": {
            "type": "chat_transcript",
            "app": source_app,
            "format": source_type,
        },
        "time": {
            "observed_at": observed_at,
        },
        "anchor": {
            "id": anchor_id,
            "type": anchor_type,
            "name": subject_kind,
            "occurred_at": utc_timestamp(),
        },
        "subject": {
            "kind": subject_kind,
            "title": title,
        },
        "content": {
            "text": content_text or "",
        },
        "context": {},
        "provenance": provenance,
        "artifacts": artifacts or [],
        "extra_data": {
            "chat": extra_chat or {},
        },
    }


def provenance_for(
    path,
    source_type,
    fingerprint,
    session_key,
    session_id,
    line_no=None,
    message_ids=None,
    tool_ids=None,
):
    provenance = {
        "source_uri": source_uri(path),
        "source_type": source_type,
        "source_fingerprint": fingerprint,
        "session_key": session_key,
    }
    if session_id:
        provenance["session_id"] = session_id
    if line_no:
        provenance["line_start"] = line_no
        provenance["line_end"] = line_no
    if message_ids:
        provenance["source_message_ids"] = message_ids
    if tool_ids:
        provenance["tool_ids"] = tool_ids
    return provenance


class ParsedSession:
    def __init__(self, source_name, source_type, path, session_key, fingerprint):
        self.source_name = source_name
        self.source_type = source_type
        self.path = Path(path)
        self.session_key = session_key
        self.fingerprint = fingerprint
        self.session_id = ""
        self.cwd = ""
        self.model = ""
        self.started_at = 0
        self.updated_at = source_updated_at_ms(path)
        self.first_user_message = ""
        self.last_user_message = ""
        self.message_count = 0
        self.tool_count = 0
        self.events = []
        self.tool_ids = set()

    def observe_timestamp(self, timestamp_ms):
        if not timestamp_ms:
            return
        if not self.started_at:
            self.started_at = timestamp_ms
        if timestamp_ms > self.updated_at:
            self.updated_at = timestamp_ms

    def observe_user_text(self, text):
        text = truncate_text(text)
        if not text:
            return
        if not self.first_user_message:
            self.first_user_message = text
        self.last_user_message = text


def parse_codex_session(path):
    path = Path(path)
    fingerprint = source_fingerprint(path)
    session = ParsedSession(
        "Codex",
        "codex_jsonl",
        path,
        f"codex-{short_hash(path.resolve())}",
        fingerprint,
    )
    pending_events = []

    for line_no, envelope in read_jsonl(path):
        timestamp_ms = iso_to_ms(envelope.get("timestamp"))
        session.observe_timestamp(timestamp_ms)
        payload = envelope.get("payload") or {}
        envelope_type = envelope.get("type")

        if envelope_type == "session_meta":
            session.session_id = payload.get("id") or session.session_id
            session.cwd = payload.get("cwd") or session.cwd
            continue

        if envelope_type == "turn_context":
            session.cwd = payload.get("cwd") or session.cwd
            session.model = payload.get("model") or session.model
            continue

        if envelope_type != "response_item" or not isinstance(payload, dict):
            continue

        item_type = payload.get("type")
        msg_id = f"codex:{session.session_id or path.stem}:{line_no}"
        observed_at = timestamp_ms or observed_time_ms()

        if item_type == "message":
            role = payload.get("role") or "assistant"
            text = text_from_content(payload.get("content"))
            if not text.strip():
                continue
            session.message_count += 1
            if role == "user":
                session.observe_user_text(text)
            pending_events.append(
                base_event(
                    COLLECTOR_ID,
                    "Codex",
                    "codex_jsonl",
                    "chat_message",
                    "chat_message",
                    msg_id,
                    observed_at,
                    f"Codex {role} message",
                    text,
                    provenance_for(
                        path,
                        "codex_jsonl",
                        fingerprint,
                        session.session_key,
                        session.session_id,
                        line_no,
                        [msg_id],
                    ),
                    extra_chat={
                        "kind": "chat_message",
                        "role": role,
                        "session_key": session.session_key,
                        "session_id": session.session_id,
                        "cwd": session.cwd,
                        "model": session.model,
                    },
                )
            )
            continue

        if item_type == "reasoning":
            thinking = text_from_content(payload.get("content")) or text_from_content(
                payload.get("summary")
            )
            if not thinking and payload.get("encrypted_content"):
                thinking = "[encrypted reasoning]"
            if not thinking.strip():
                continue
            session.message_count += 1
            pending_events.append(
                base_event(
                    COLLECTOR_ID,
                    "Codex",
                    "codex_jsonl",
                    "chat_message",
                    "chat_message",
                    msg_id,
                    observed_at,
                    "Codex assistant reasoning",
                    "",
                    provenance_for(
                        path,
                        "codex_jsonl",
                        fingerprint,
                        session.session_key,
                        session.session_id,
                        line_no,
                        [msg_id],
                    ),
                    extra_chat={
                        "kind": "chat_message",
                        "role": "assistant",
                        "thinking": thinking,
                        "session_key": session.session_key,
                        "session_id": session.session_id,
                    },
                )
            )
            continue

        if item_type in {"function_call", "custom_tool_call"}:
            tool_id = payload.get("call_id") or f"tool-{line_no}"
            session.tool_ids.add(tool_id)
            session.tool_count = len(session.tool_ids)
            tool_input = payload.get("input")
            if item_type == "function_call":
                tool_input = any_from_json_string(payload.get("arguments"))
            pending_events.append(
                base_event(
                    COLLECTOR_ID,
                    "Codex",
                    "codex_jsonl",
                    "chat_tool_call",
                    "chat_tool_call",
                    tool_id,
                    observed_at,
                    payload.get("name") or "Codex tool call",
                    "",
                    provenance_for(
                        path,
                        "codex_jsonl",
                        fingerprint,
                        session.session_key,
                        session.session_id,
                        line_no,
                        [msg_id],
                        [tool_id],
                    ),
                    extra_chat={
                        "kind": "chat_tool_call",
                        "tool_id": tool_id,
                        "tool_name": payload.get("name") or "",
                        "tool_input": tool_input,
                        "status": payload.get("status") or "",
                        "session_key": session.session_key,
                        "session_id": session.session_id,
                    },
                )
            )
            continue

        if item_type in {"function_call_output", "custom_tool_call_output"}:
            tool_id = payload.get("call_id") or f"tool-{line_no}"
            session.tool_ids.add(tool_id)
            session.tool_count = len(session.tool_ids)
            pending_events.append(
                base_event(
                    COLLECTOR_ID,
                    "Codex",
                    "codex_jsonl",
                    "chat_tool_result",
                    "chat_tool_result",
                    tool_id,
                    observed_at,
                    "Codex tool result",
                    "",
                    provenance_for(
                        path,
                        "codex_jsonl",
                        fingerprint,
                        session.session_key,
                        session.session_id,
                        line_no,
                        [msg_id],
                        [tool_id],
                    ),
                    extra_chat={
                        "kind": "chat_tool_result",
                        "tool_id": tool_id,
                        "tool_output": payload.get("output"),
                        "session_key": session.session_key,
                        "session_id": session.session_id,
                    },
                )
            )

    if not session.session_id:
        session.session_id = path.stem
    session.events = [
        build_summary_event(session),
        *pending_events,
    ]
    return session


def decode_claude_project_dir(name):
    return "/" + name.lstrip("-").replace("-", "/")


def parse_claude_content(projects_root, project_dir, session, raw, tool_result_raw, line_no):
    if raw is None:
        return "", "", []
    if isinstance(raw, str):
        return raw, "", []
    if not isinstance(raw, list):
        return "", "", []

    texts = []
    thinking = []
    tool_events = []
    for block in raw:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and block.get("text"):
            texts.append(str(block["text"]))
        elif block_type == "thinking" and block.get("thinking"):
            thinking.append(str(block["thinking"]))
        elif block_type == "tool_use":
            tool_id = block.get("id") or f"tool-{line_no}"
            session.tool_ids.add(tool_id)
            tool_events.append(
                {
                    "kind": "chat_tool_call",
                    "tool_id": tool_id,
                    "tool_name": block.get("name") or "",
                    "tool_input": block.get("input"),
                    "line_no": line_no,
                }
            )
        elif block_type == "tool_result":
            tool_id = block.get("tool_use_id") or f"tool-{line_no}"
            session.tool_ids.add(tool_id)
            artifacts = []
            output = block.get("content")
            external = block.get("external") or {}
            external_id = external.get("id")
            if external_id and session.session_id:
                result_path = (
                    Path(projects_root)
                    / project_dir
                    / session.session_id
                    / "tool-results"
                    / f"{external_id}.txt"
                )
                if result_path.exists():
                    artifacts.append(
                        file_artifact(
                            f"claude_tool_result_{external_id}",
                            "external_tool_result",
                            result_path,
                            source_fingerprint(result_path),
                        )
                    )
            if output is None and tool_result_raw is not None:
                output = tool_result_raw
            tool_events.append(
                {
                    "kind": "chat_tool_result",
                    "tool_id": tool_id,
                    "tool_output": output,
                    "is_error": bool(block.get("is_error")),
                    "artifacts": artifacts,
                    "line_no": line_no,
                }
            )
    session.tool_count = len(session.tool_ids)
    return "\n\n".join(texts), "\n\n".join(thinking), tool_events


def parse_claude_session(projects_root, path):
    path = Path(path)
    projects_root = Path(projects_root)
    project_dir = path.parent.name
    fingerprint = source_fingerprint(path)
    session = ParsedSession(
        "Claude",
        "claude_jsonl",
        path,
        f"claude-{short_hash(path.resolve())}",
        fingerprint,
    )
    pending_events = []

    for line_no, msg in read_jsonl(path):
        timestamp_ms = iso_to_ms(msg.get("timestamp"))
        session.observe_timestamp(timestamp_ms)
        session.session_id = msg.get("sessionId") or session.session_id
        session.cwd = msg.get("cwd") or session.cwd
        message = msg.get("message") or {}
        if not isinstance(message, dict):
            continue
        if message.get("model"):
            session.model = message.get("model")
        role = message.get("role") or msg.get("type") or "unknown"
        msg_id = msg.get("uuid") or f"claude:{session.session_id or path.stem}:{line_no}"
        text, thinking, tool_events = parse_claude_content(
            projects_root,
            project_dir,
            session,
            message.get("content"),
            msg.get("toolUseResult"),
            line_no,
        )
        observed_at = timestamp_ms or observed_time_ms()
        if text.strip() or thinking.strip():
            session.message_count += 1
            if role == "user":
                session.observe_user_text(text)
            pending_events.append(
                base_event(
                    COLLECTOR_ID,
                    "Claude",
                    "claude_jsonl",
                    "chat_message",
                    "chat_message",
                    msg_id,
                    observed_at,
                    f"Claude {role} message",
                    text,
                    provenance_for(
                        path,
                        "claude_jsonl",
                        fingerprint,
                        session.session_key,
                        session.session_id,
                        line_no,
                        [msg_id],
                    ),
                    extra_chat={
                        "kind": "chat_message",
                        "role": role,
                        "thinking": thinking,
                        "session_key": session.session_key,
                        "session_id": session.session_id,
                        "cwd": session.cwd,
                        "model": session.model,
                    },
                )
            )
        for tool_event in tool_events:
            tool_id = tool_event["tool_id"]
            kind = tool_event["kind"]
            pending_events.append(
                base_event(
                    COLLECTOR_ID,
                    "Claude",
                    "claude_jsonl",
                    kind,
                    kind,
                    tool_id,
                    observed_at,
                    tool_event.get("tool_name") or f"Claude {kind}",
                    "",
                    provenance_for(
                        path,
                        "claude_jsonl",
                        fingerprint,
                        session.session_key,
                        session.session_id,
                        tool_event["line_no"],
                        [msg_id],
                        [tool_id],
                    ),
                    artifacts=tool_event.get("artifacts") or [],
                    extra_chat={
                        "kind": kind,
                        "tool_id": tool_id,
                        "tool_name": tool_event.get("tool_name") or "",
                        "tool_input": tool_event.get("tool_input"),
                        "tool_output": tool_event.get("tool_output"),
                        "is_error": tool_event.get("is_error", False),
                        "session_key": session.session_key,
                        "session_id": session.session_id,
                    },
                )
            )

    if not session.session_id:
        session.session_id = path.stem
    if not session.cwd:
        session.cwd = decode_claude_project_dir(project_dir)
    session.events = [
        build_summary_event(session),
        *pending_events,
    ]
    return session


def build_summary_event(session):
    observed_at = session.updated_at or observed_time_ms()
    title = session.first_user_message or session.session_id or session.session_key
    artifacts = [
        file_artifact(
            "raw_transcript",
            "source_transcript",
            session.path,
            session.fingerprint,
        )
    ]
    return base_event(
        COLLECTOR_ID,
        session.source_name,
        session.source_type,
        "chat_session_summary",
        "chat_session",
        session.session_key,
        observed_at,
        title,
        session.first_user_message,
        provenance_for(
            session.path,
            session.source_type,
            session.fingerprint,
            session.session_key,
            session.session_id,
        ),
        artifacts=artifacts,
        extra_chat={
            "kind": "chat_session_summary",
            "session_key": session.session_key,
            "session_id": session.session_id,
            "cwd": session.cwd,
            "model": session.model,
            "started_at": session.started_at,
            "updated_at": session.updated_at,
            "message_count": session.message_count,
            "tool_count": session.tool_count,
            "first_user_message": session.first_user_message,
            "last_user_message": session.last_user_message,
        },
    )


def load_checkpoint(work_dir):
    path = Path(work_dir) / "state" / "checkpoint.json"
    if not path.exists():
        return {"version": 1, "sources": {}}
    try:
        with path.open(encoding="utf-8") as checkpoint_file:
            checkpoint = json.load(checkpoint_file)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "sources": {}}
    checkpoint.setdefault("version", 1)
    checkpoint.setdefault("sources", {})
    return checkpoint


def save_checkpoint(work_dir, checkpoint):
    path = Path(work_dir) / "state" / "checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as checkpoint_file:
        json.dump(checkpoint, checkpoint_file, ensure_ascii=True, indent=2, sort_keys=True)
        checkpoint_file.write("\n")


def discover_codex_files(root):
    root = Path(root).expanduser()
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.jsonl") if path.is_file())


def discover_claude_files(root):
    root = Path(root).expanduser()
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*.jsonl")
        if path.is_file() and "tool-results" not in path.parts
    )


def output_path_for(work_dir, observed_at):
    day_dir = Path(work_dir) / datetime.fromtimestamp(observed_at / 1000).strftime(
        "%Y%m%d"
    )
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"{observed_at}.jsonl"


def write_events(work_dir, events):
    if not events:
        return None
    observed_at = observed_time_ms()
    output_path = output_path_for(work_dir, observed_at)
    with output_path.open("a", encoding="utf-8") as output_file:
        for event in events:
            output_file.write(json.dumps(event, ensure_ascii=True, sort_keys=True))
            output_file.write("\n")
    return output_path


def scan_sources(args, work_dir):
    checkpoint = load_checkpoint(work_dir)
    changed_sessions = []
    errors = []
    roots = {
        "codex_sessions_readable": Path(args.codex_sessions_root).expanduser().exists(),
        "claude_projects_readable": Path(args.claude_projects_root).expanduser().exists(),
    }
    candidates = []
    for path in discover_codex_files(args.codex_sessions_root):
        candidates.append(("codex_jsonl", path))
    for path in discover_claude_files(args.claude_projects_root):
        candidates.append(("claude_jsonl", path))

    for source_type, path in candidates:
        try:
            fingerprint = source_fingerprint(path)
        except OSError as error:
            errors.append(f"{path}: {error}")
            continue
        key = source_uri(path)
        previous = checkpoint["sources"].get(key, {})
        if not args.force and previous.get("source_fingerprint") == fingerprint:
            continue
        try:
            if source_type == "codex_jsonl":
                session = parse_codex_session(path)
            else:
                session = parse_claude_session(args.claude_projects_root, path)
        except Exception as error:
            errors.append(f"{path}: {error}")
            checkpoint["sources"][key] = {
                "source_uri": key,
                "source_type": source_type,
                "source_fingerprint": fingerprint,
                "last_error": str(error),
                "last_scan_at": observed_time_ms(),
            }
            continue
        if len(session.events) > 1 or args.emit_empty_sessions:
            changed_sessions.append(session)
        checkpoint["sources"][key] = {
            "source_uri": key,
            "source_type": source_type,
            "source_fingerprint": fingerprint,
            "session_key": session.session_key,
            "session_id": session.session_id,
            "last_processed_line": count_lines(path),
            "last_scan_at": observed_time_ms(),
            "last_error": "",
        }

    events = []
    for session in changed_sessions:
        events.extend(session.events)
    output_path = write_events(work_dir, events)
    checkpoint["last_scan_completed_at"] = observed_time_ms()
    save_checkpoint(work_dir, checkpoint)
    return {
        "permissions": roots,
        "sessions_seen": len(candidates),
        "sessions_changed": len(changed_sessions),
        "events_written": len(events),
        "output_path": str(output_path) if output_path else "",
        "last_error": "; ".join(errors[-3:]),
    }


def count_lines(path):
    with Path(path).open(encoding="utf-8") as data_file:
        return sum(1 for _ in data_file)


def register_collector(client):
    return client.call(
        "collector:register",
        {
            "collector_id": COLLECTOR_ID,
            "protocol_version": 1,
            "capabilities": [
                "codex_jsonl",
                "claude_jsonl",
                "incremental_scan",
                "source_provenance",
                "artifact_pointer",
                "heartbeat",
            ],
            "metadata": {
                "display_name": DISPLAY_NAME,
            },
        },
        timeout=5,
    )


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def connect_client(client, coordinator_url, timeout):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            client.connect(coordinator_url)
            return
        except socketio.exceptions.ConnectionError as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"could not connect to coordinator: {last_error}")


def run(args):
    client = socketio.Client()
    connect_client(client, args.coordinator_url, args.connect_timeout)
    try:
        registration = register_collector(client)
        if not registration.get("ok"):
            raise RuntimeError(f"collector registration failed: {registration}")
        work_dir = registration["work_dir"]
        print(f"registered {COLLECTOR_ID}")

        last_scan = {}
        deadline = time.monotonic() + max(args.duration, 0)
        next_scan_at = 0
        while True:
            now = time.monotonic()
            if now >= next_scan_at:
                started_at = observed_time_ms()
                last_scan = scan_sources(args, work_dir)
                last_scan["last_scan_started_at"] = started_at
                if last_scan.get("output_path"):
                    print(f"wrote collected chat data: {last_scan['output_path']}")
                next_scan_at = now + max(args.scan_interval, 1)

            client.call(
                "collector:heartbeat",
                {
                    "status": "error" if last_scan.get("last_error") else "ok",
                    "permissions": last_scan.get("permissions", {}),
                    "last_scan_started_at": last_scan.get("last_scan_started_at", 0),
                    "last_scan_completed_at": observed_time_ms(),
                    "sessions_seen": last_scan.get("sessions_seen", 0),
                    "sessions_changed": last_scan.get("sessions_changed", 0),
                    "events_written": last_scan.get("events_written", 0),
                    "last_write": last_scan.get("output_path", ""),
                    "last_error": last_scan.get("last_error", ""),
                },
                timeout=5,
            )

            if args.duration <= 0 or time.monotonic() >= deadline:
                break
            sleep_for = min(
                max(args.heartbeat_interval, 0.1),
                max(deadline - time.monotonic(), 0),
            )
            if sleep_for:
                time.sleep(sleep_for)
    finally:
        client.disconnect()


def parse_args():
    home = Path.home()
    parser = argparse.ArgumentParser(
        description="Register a Radar chat transcript collector and write collected JSONL events."
    )
    parser.add_argument(
        "--coordinator-url",
        default=os.environ.get("RADAR_COORDINATOR_URL", "http://localhost:5000"),
        help="Socket.IO coordinator URL.",
    )
    parser.add_argument(
        "--codex-sessions-root",
        default=os.environ.get(
            "RADAR_CHAT_CODEX_SESSIONS_ROOT", str(home / ".codex" / "sessions")
        ),
        help="Codex transcript root.",
    )
    parser.add_argument(
        "--claude-projects-root",
        default=os.environ.get(
            "RADAR_CHAT_CLAUDE_PROJECTS_ROOT", str(home / ".claude" / "projects")
        ),
        help="Claude projects transcript root.",
    )
    parser.add_argument(
        "--duration",
        default=env_float("RADAR_CHAT_COLLECTOR_DURATION", 10),
        type=float,
        help="Seconds to stay registered before disconnecting.",
    )
    parser.add_argument(
        "--scan-interval",
        default=env_float("RADAR_CHAT_SCAN_INTERVAL", 300),
        type=float,
        help="Seconds between transcript scans.",
    )
    parser.add_argument(
        "--heartbeat-interval",
        default=env_float("RADAR_CHAT_HEARTBEAT_INTERVAL", 2),
        type=float,
        help="Seconds between collector heartbeats while registered.",
    )
    parser.add_argument(
        "--connect-timeout",
        default=env_float("RADAR_COLLECTOR_CONNECT_TIMEOUT", 10),
        type=float,
        help="Seconds to wait for the coordinator before failing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=env_bool("RADAR_CHAT_FORCE", False),
        help="Re-emit all sessions even if fingerprints are unchanged.",
    )
    parser.add_argument(
        "--emit-empty-sessions",
        action="store_true",
        default=env_bool("RADAR_CHAT_EMIT_EMPTY_SESSIONS", False),
        help="Emit session summaries even when no message/tool events were parsed.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
