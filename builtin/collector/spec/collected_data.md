# Collected Data Spec

Collected data is written by collectors into the `work_dir` returned by
`collector:register`. Data is stored as JSON Lines. Each line is one collected
event.

## Storage Layout

```text
/{work_dir}
    /yyyymmdd
        /artifacts
        - 1780713574000.jsonl
```

- `work_dir` is the collector-specific folder assigned by the coordinator.
- `yyyymmdd` is the collection date in local time.
- Artifact files are split by time to keep individual files small.

## Event Shape

Field names must use snake_case.

Only `id`, `collector_id`, `source`, and `time.observed_at` are required. Other
maps can be expanded by each collector when more detail is available.

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
    "artifacts": [
        {
            "id": "artifact_1",
            "kind": "screenshot",
            "uri": "vault://artifacts/artifact_1",
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

## Runtime Storage

Collectors do not submit collected event payloads over Socket.IO. After
registration, the coordinator returns a `work_dir`, and the collector owns data
capture, JSONL writes, artifact writes, rotation, and local cleanup within that
folder.

The collector is responsible for:

- Writing events in the shape above.
- Writing artifacts under the same assigned work folder.
- Rotating files so individual JSONL files stay small.
- Reporting health and high-level status through `collector:heartbeat`.

Processors should receive folder or file references from the coordinator and
read collected data from disk. Collectors should not write directly to processor
input queues.
