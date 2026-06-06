# Processor Spec

A processor is a standalone module that receives folder or file references from
the coordinator process, transforms or analyzes collected data, and returns
results to the coordinator.

Processors may filter noisy data, redact sensitive content, normalize raw
observations, detect repeated patterns, generate predictions, or learn from user
feedback.

## Responsibilities

- The processor connects to the coordinator Socket.IO server.
- The processor registers its identity, input shapes, output shapes, and
  capabilities.
- The processor receives collector work folder or file references and feedback
  from the coordinator, or scans a configured collector root when run in
  standalone mode.
- The processor identifies collector data it can use by event shape,
  provenance, and declared subject kind. It must not create collector-owned
  folders or perform collection work itself.
- The processor emits results back to the coordinator.
- The processor preserves input provenance so downstream actors, UI, and audit
  views can trace a result back to collected events and source artifacts.

Processors should not talk directly to collectors or actors. The coordinator
owns routing, policy, persistence, and user-visible side effects.

## Registration

After opening a Socket.IO connection, a processor must emit
`processor:register`.

```json
{
    "processor_id": "builtin.pattern_detector",
    "protocol_version": 1,
    "accepts": ["collector_work_dir", "collector_file"],
    "produces": ["normalized_action", "prediction_set", "user_suggestion"],
    "output_targets": ["actor"],
    "capabilities": ["filtering", "pattern_detection"]
}
```

`output_targets` declares where the processor output is intended to be consumed.
Use a list so mixed processors can opt into more than one target:

- `actor`: processor results are intended for downstream actor evaluation or
  action routing.
- `skill`: processor results are intended to update a skill-like knowledge
  folder.

The coordinator acknowledges registration with:

```json
{
    "ok": true,
    "role": "processor",
    "processor_id": "builtin.pattern_detector"
}
```

## Socket.IO Events

Processors emit these events to the coordinator:

- `processor:register`: register processor identity and capabilities.
- `processor:heartbeat`: report liveness and current status.
- `processor:result`: submit normalized actions, predictions, suggestions, or
  learning results.

The coordinator may emit these events to processors:

- `coordinator:collector_registered`: deliver a collector ID and assigned work
  folder after collector registration.
- `coordinator:process_request`: request processing for a collector work folder
  or specific collected data file.
- `coordinator:user_feedback`: deliver feedback for learning.
- `coordinator:processor_config`: send updated processor config.
- `coordinator:processor_pause`: pause processing.
- `coordinator:processor_resume`: resume processing.

## Result Shape

Field names must use snake_case.

```json
{
    "id": "uuid",
    "processor_id": "builtin.pattern_detector",
    "input_refs": ["collector:macos.axtree:20260606/1780713574000.jsonl"],
    "source_refs": [
        {
            "collector_id": "chat.transcript",
            "event_id": "uuid",
            "source_uri": "file:///Users/example/.codex/sessions/2026/06/06/session.jsonl",
            "source_fingerprint": "12345:1780713574000000000",
            "session_key": "codex-abc123",
            "source_message_ids": ["codex:session_uuid:42"],
            "tool_ids": ["call_abc"]
        }
    ],
    "kind": "user_suggestion",
    "created_at": "2026-06-06T10:12:05Z",
    "confidence": 0.82,
    "privacy": {
        "contains_raw_content": false,
        "redaction_applied": true
    },
    "payload": {
        "title": "Prepare deployment checklist",
        "reason": "Similar deployment questions appeared three times today."
    }
}
```

The coordinator owns persistence, policy checks, UI presentation, and actor
eligibility decisions for processor results.

## Replay And Targeted Lookup

Processors should avoid copying large raw transcripts or tool outputs into
results. Prefer compact payloads plus `source_refs` that point back to collected
events, source transcript lines, and artifact IDs. If a model or actor needs
exact source material, the processor should resolve it from the collector
`work_dir` and validate the source fingerprint before use.

For chat transcript processing, a recommended pipeline is:

1. Discover candidate collector work folders under the configured collectors
   root, or use coordinator-provided collector folder/file references.
2. Select transcript-like events by subject kind, chat metadata, and provenance,
   regardless of which collector produced them.
3. Build a compact normalized session that includes user turns, final assistant
   responses, message IDs, tool call summaries, and files touched.
4. Keep exact tool arguments and tool results as artifacts or cache files for
   targeted lookup.
5. Check a processor-owned checkpoint keyed by normalized source provenance so
   unchanged sessions are skipped.
6. Run filtering, redaction, extraction, prediction, or learning processors.
7. Emit result `source_refs` that preserve session keys, message IDs, tool IDs,
   and source fingerprints.
