# Collected Data Spec

Collected data is written by collectors into the `work_dir` returned by
`collector:register`. Data is stored as JSON Lines. Each line is one collected
event.

## Storage Layout

```text
/{work_dir}
    /state
        - checkpoint.json
    /yyyymmdd
        /artifacts
            /{event_or_session_id}
        - {file_timestamp_ms}.jsonl
```

- `work_dir` is the collector-specific folder assigned by the coordinator.
- `yyyymmdd` is the collection date in local time.
- JSONL file grouping is collector-defined. A collector may split files by
  size, event count, session, source, time bucket, or another source-appropriate
  strategy, as long as each file remains reasonably small and discoverable under
  `work_dir`.
- JSONL file names should be timestamps in epoch milliseconds. The timestamp
  identifies the collector's chosen file or bucket boundary, such as file-open
  time, first-event time, or time-bucket start. It does not require one output
  file per event.
- `state/checkpoint.json` is optional collector-owned state for incremental
  scans, source fingerprints, and cleanup bookkeeping.
- `artifacts` contains copied artifacts or small metadata files for external
  artifact pointers.

## Event Shape

Field names must use snake_case.

Only `id`, `collector_id`, `source`, and `time.observed_at` are required. Other
maps can be expanded by each collector when more detail is available.

`time.observed_at` and other event timestamps are sequence metadata for later
processors. They help reconstruct time-series order, but they do not define the
file boundary or storage rotation policy.

```json
{
    "id": "uuid",
    "collector_id": "macos.axtree",
    "source": {
        "type": "macos",
        "app": "Google Chrome",
        "bundle_id": "com.google.Chrome"
    },
    "time": {
        "observed_at": 1780713574000
    },
    "anchor": {
        "id": "anchor_456",
        "type": "user_action",
        "name": "mouse_click",
        "occurred_at": "2026-06-06T10:12:04Z",
        "target": {
            "app": "Google Chrome",
            "window_title": "ChatGPT",
            "element_role": "AXButton",
            "element_title": "Send"
        }
    },
    "subject": {
        "kind": "window",
        "title": "ChatGPT",
        "url": "https://chatgpt.com"
    },
    "content": {
        "text": "How do I deploy a Go service to Kubernetes?"
    },
    "context": {
        "active_app": "Google Chrome",
        "active_window_title": "ChatGPT",
        "user_action": "clicked"
    },
    "provenance": {
        "source_uri": "file:///Users/example/.codex/sessions/2026/06/06/session.jsonl",
        "source_type": "codex_jsonl",
        "source_fingerprint": "12345:1780713574000000000",
        "session_key": "codex-abc123",
        "session_id": "session_uuid",
        "line_start": 42,
        "line_end": 42,
        "source_message_ids": ["codex:session_uuid:42"],
        "tool_ids": ["call_abc"]
    },
    "artifacts": [
        {
            "id": "artifact_1",
            "kind": "screenshot",
            "uri": "vault://artifacts/artifact_1",
            "storage": "copied",
            "mime_type": "image/png",
            "size_bytes": 1234567
        }
    ],
    "extra_data": {
        "axtree": {
            "app": "Google Chrome",
            "window_title": "ChatGPT",
            "selected_text": "",
            "visible_texts": [
                "How do I deploy a Go service to Kubernetes?",
                "Send"
            ],
            "focused_element": {
                "role": "AXTextArea",
                "value": "How do I deploy a Go service to Kubernetes?"
            }
        }
    }
}
```

## Provenance

`provenance` is the trace-back contract between collectors and processors.
Collectors should include it whenever the source has durable identifiers. The map
is intentionally extensible, but these fields are recommended:

- `source_uri`: original local file, URL, app resource, or database URI.
- `source_type`: source-specific format such as `codex_jsonl`,
  `claude_jsonl`, `macos_axtree`, or `browser_dom`.
- `source_fingerprint`: cheap change detector for the source, such as
  `size:mtime_unix_nano`, content hash, or source revision.
- `session_key`: stable Radar key for a source session.
- `session_id`: source-native session ID when available.
- `line_start` and `line_end`: JSONL line range or equivalent source offset.
- `source_message_ids`: source or Radar-normalized message IDs.
- `tool_ids`: source or Radar-normalized tool call IDs.

Processors should copy relevant provenance into `input_refs` or result payloads
instead of inventing new evidence identifiers.

## Artifact References

Artifacts can be copied into `work_dir` or left in place and referenced by
pointer. Use `storage` to make this explicit:

- `copied`: collector copied the artifact under `work_dir`.
- `external_pointer`: artifact remains at `uri`; processors must verify the
  fingerprint before relying on it.
- `derived`: artifact was produced by the collector from source data, such as a
  normalized transcript or compact session preview.

Pointer artifacts are useful for large or already-durable sources such as Codex
transcript files, Claude transcript files, and Claude external `tool-results`
files. They should include `source_fingerprint` or equivalent metadata in
`extra_data` when possible.

## Chat Event Kinds

Chat transcript collectors should use the shared event shape and identify chat
events through `subject.kind`, `anchor.type`, or `extra_data.chat.kind`. Common
kinds are:

- `chat_session_summary`
- `chat_message`
- `chat_tool_call`
- `chat_tool_result`
- `normalized_chat_session`

## macOS Activity Event Kinds

macOS activity collectors should treat native input signals as action anchors,
not as raw input logs. A collector may listen for important triggers such as
mouse clicks and Enter/Return key presses, then emit one context-rich observation
for each trigger.

Common `anchor.name` values are:

- `mouse_click`
- `enter_key`

The collector should capture the active app/window/document and focused
accessibility element around the trigger under `extra_data.macos`. It should not
store raw keystroke streams. Focused element values or selected text, when
captured, should be redacted for sensitive controls and bounded in length.

## Runtime Storage

Collectors do not submit collected event payloads over Socket.IO. After
registration, the coordinator returns a `work_dir`, and the collector owns data
capture, JSONL writes, artifact writes, rotation, and local cleanup within that
folder.

The collector is responsible for:

- Writing events in the shape above.
- Writing artifacts under the same assigned work folder.
- Choosing a file grouping and rotation strategy that keeps individual JSONL
  files small, then naming each file with the timestamp for that chosen file or
  bucket boundary.
- Reporting health and high-level status through `collector:heartbeat`.

Processors should receive folder or file references from the coordinator and
read collected data from disk. Collectors should not write directly to processor
input queues.
