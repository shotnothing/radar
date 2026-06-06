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
  from the coordinator.
- The processor emits results back to the coordinator.

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
    "capabilities": ["filtering", "pattern_detection"]
}
```

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
    "input_refs": ["collector:macos.axtree:1780713574000.jsonl"],
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
