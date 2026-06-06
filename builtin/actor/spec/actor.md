# Actor Spec

An actor is a standalone module that prepares or executes an approved action.
Actors receive action requests from the coordinator over Socket.IO and return
execution status.

Actors may draft messages, prepare files, open URLs, stage commands, or run
approved automations. They must not decide on their own that an action is
eligible; the coordinator owns policy and user approval.

## Responsibilities

- The actor connects to the coordinator Socket.IO server.
- The actor registers its identity, accepted action kinds, safety level, and
  capabilities.
- The actor receives action requests from the coordinator.
- The actor emits execution results back to the coordinator.

Actors should not talk directly to collectors or processors.

## Registration

After opening a Socket.IO connection, an actor must emit `actor:register`.

```json
{
    "actor_id": "builtin.browser_opener",
    "protocol_version": 1,
    "accepts": ["open_url"],
    "safety_level": "prepare",
    "capabilities": ["open_url", "stage_action"]
}
```

The coordinator acknowledges registration with:

```json
{
    "ok": true,
    "role": "actor",
    "actor_id": "builtin.browser_opener"
}
```

## Socket.IO Events

Actors emit these events to the coordinator:

- `actor:register`: register actor identity and capabilities.
- `actor:heartbeat`: report liveness and current status.
- `actor:result`: submit prepared, completed, failed, or cancelled action
  status.

The coordinator may emit these events to actors:

- `coordinator:action_request`: request that an actor prepare or execute an
  approved action.
- `coordinator:actor_config`: send updated actor config.
- `coordinator:actor_pause`: pause actor execution.
- `coordinator:actor_resume`: resume actor execution.

## Result Shape

Field names must use snake_case.

```json
{
    "id": "uuid",
    "actor_id": "builtin.browser_opener",
    "request_id": "action_request_uuid",
    "status": "prepared",
    "created_at": "2026-06-06T10:12:06Z",
    "payload": {
        "summary": "URL is ready to open after confirmation."
    }
}
```
