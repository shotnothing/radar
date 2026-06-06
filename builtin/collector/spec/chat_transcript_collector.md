# Chat Transcript Collector Spec

A chat transcript collector observes local agent chat transcript files and writes
replayable Radar collected data. The first supported sources should be Codex and
Claude because both already persist local JSONL transcripts.

## Goals

- Collect user/assistant chat sessions from Codex and Claude as a collector,
  before any processor performs filtering or learning extraction.
- Preserve traceability to the original transcript file, line number, message
  ID, tool call ID, and external tool-result artifact.
- Save data in the shared collected-data JSONL shape so processors can replay,
  compact, redact, and analyze sessions independently.
- Support incremental rescans through source fingerprints and collector
  checkpoints.

## Source Locations

Default source roots:

```text
~/.codex/sessions/**/*.jsonl
~/.claude/projects/**/*.jsonl
~/.claude/projects/**/tool-results/*
```

The collector should treat these paths as configurable. Missing roots are not an
error; the collector should report them in heartbeat status and continue with
available sources.

## Collector Identity

Recommended `meta.json` fields:

```json
{
    "collector_id": "chat.transcript",
    "display_name": "Chat Transcript Collector",
    "description": "Collects local Codex and Claude JSONL transcripts with source provenance.",
    "protocol_version": 1,
    "runtime": {
        "command": "python3",
        "args": ["builtin/collector/chat_transcript/collector.py"]
    },
    "required_permissions": ["filesystem_read_home"],
    "capabilities": [
        "codex_jsonl",
        "claude_jsonl",
        "incremental_scan",
        "source_provenance",
        "artifact_pointer"
    ],
    "emits": [
        "collected_data.chat_session_summary",
        "collected_data.chat_message",
        "collected_data.chat_tool_call",
        "collected_data.chat_tool_result"
    ],
    "default_config": {
        "codex_sessions_root": "~/.codex/sessions",
        "claude_projects_root": "~/.claude/projects",
        "scan_interval_seconds": 300,
        "copy_raw_transcripts": false
    }
}
```

## Incremental Scan

The collector should keep `state/checkpoint.json` under its assigned `work_dir`.
The checkpoint should record, per source file:

- source URI.
- source type.
- stable session key.
- source fingerprint, preferably `size:mtime_unix_nano`.
- last processed line number or byte offset when supported.
- last successful collection time.
- any parse error state.

If a file fingerprint has not changed, the collector may skip it. If a file
changes, the collector may append only new lines when line offsets are known, or
re-emit a new `chat_session_summary` and changed message events with the updated
fingerprint.

## Stable Keys

Use stable Radar keys so processors and UI can re-find sessions:

```text
codex-<short_hash(source_path)>
claude-<short_hash(source_path)>
```

The source-native session ID should be stored separately in
`provenance.session_id` and `extra_data.chat.session_id`.

## Event Mapping

Each source transcript should produce at least one `chat_session_summary` event.
Message-level events should be emitted when available and useful for processors.

Recommended fields:

- `source.type`: `chat_transcript`.
- `source.app`: `Codex` or `Claude`.
- `subject.kind`: one of the chat event kinds from `collected_data.md`.
- `subject.title`: first user message preview or source session ID.
- `content.text`: message text or compact summary preview.
- `anchor.type`: `chat_session`, `chat_message`, `chat_tool_call`, or
  `chat_tool_result`.
- `anchor.id`: stable message/tool/session ID.
- `provenance`: source URI, fingerprint, session key, source line range,
  source message IDs, and tool IDs.
- `artifacts`: raw transcript pointer, external tool-result pointer, or derived
  normalized session artifact.

## Codex JSONL Mapping

Codex transcript lines are envelopes with `timestamp`, `type`, and `payload`.
The collector should handle at least:

- `session_meta`: source session ID, cwd, originator, CLI version, provider.
- `turn_context`: cwd, model, effort, turn ID.
- `response_item` with payload type `message`: user or assistant message text.
- `response_item` with payload type `reasoning`: assistant thinking summary or
  encrypted reasoning marker.
- `response_item` with payload type `function_call` or `custom_tool_call`: tool
  name, call ID, arguments or input.
- `response_item` with payload type `function_call_output` or
  `custom_tool_call_output`: tool output by call ID.

Message IDs should be deterministic, for example:

```text
codex:{session_id}:{line_number}
```

## Claude JSONL Mapping

Claude transcript lines may include top-level `sessionId`, `uuid`, `timestamp`,
`cwd`, `gitBranch`, `version`, `message`, and `toolUseResult`.

The collector should handle:

- string message content.
- content blocks of type `text`.
- content blocks of type `thinking`.
- content blocks of type `tool_use`, preserving ID, name, and input.
- content blocks of type `tool_result`, preserving `tool_use_id`, inline
  content, error state, and external result references.

Claude project directory names can be decoded to a cwd by replacing leading
hyphen path encoding with `/` separators when `cwd` is absent.

External tool results should be represented as artifact pointers when present:

```text
~/.claude/projects/{project_dir}/{session_id}/tool-results/{external_id}.txt
```

## Artifact Strategy

By default, the collector should not copy raw transcript files. It should write
`external_pointer` artifacts with file URIs and source fingerprints. Copying raw
transcripts can be enabled later for backup, privacy review, or source retention.

Recommended artifacts:

- `raw_transcript`: pointer to the Codex or Claude JSONL file.
- `external_tool_result`: pointer to Claude external tool-result files.
- `normalized_session`: optional derived JSON artifact with normalized messages
  and tool refs.
- `tool_detail`: optional derived JSON artifact with parsed tool input/output.

## Processor Boundary

The collector should not decide whether a session is useful, coding-related, or
safe to learn from. That belongs to processors. The collector may perform light
source parsing and normalization only to make events replayable.

Processors should own:

- repo context detection.
- files-touched extraction from tool data.
- compact session generation.
- redaction and privacy policy.
- cheap filtering.
- learning extraction, prediction, and suggestion generation.

## Heartbeat Status

Collector heartbeats should include high-level state only:

```json
{
    "status": "ok",
    "permissions": {
        "codex_sessions_readable": true,
        "claude_projects_readable": true
    },
    "last_scan_started_at": 1780713574000,
    "last_scan_completed_at": 1780713575000,
    "sessions_seen": 42,
    "sessions_changed": 3,
    "last_error": ""
}
```
