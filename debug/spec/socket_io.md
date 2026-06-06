# Debug Socket.IO Protocol

The debug harness hosts a small local Socket.IO server for developing collectors,
processors, and actors before the Wails desktop app exists.

Default endpoint:

```text
http://localhost:5000
```

## Roles

- `debug`: optional debug client that watches registry and routing events.
- `collector`: source-specific process that observes user activity.
- `processor`: process that transforms collected data into normalized actions,
  predictions, suggestions, or learning updates.
- `actor`: process that prepares or executes approved action requests.

## Connection Flow

1. A module connects to the debug Socket.IO server.
2. The harness emits `coordinator:hello`.
3. The module registers with `collector:register`, `processor:register`, or
   `actor:register`.
4. The harness acknowledges registration.
5. The harness assigns each collector a `work_dir` in its register ack.
6. Collectors write JSONL and artifacts under their assigned `work_dir`.
7. Processors read folder or file references from coordinator messages.
8. Processors send results. The harness routes them to debug clients and sends
   action requests to actors.
9. Actors send execution status back to the harness.

## Events To Coordinator

- `debug:register`: register a debug client.
- `debug:command`: send an event to one role or one registered module.
- `collector:register`: register a collector.
- `collector:heartbeat`: update collector liveness and permission state.
- `processor:register`: register a processor.
- `processor:heartbeat`: update processor liveness.
- `processor:result`: submit processor output.
- `actor:register`: register an actor.
- `actor:heartbeat`: update actor liveness.
- `actor:result`: submit actor output.

## Events From Coordinator

- `coordinator:hello`: announce protocol readiness after connect.
- `coordinator:collector_registered`: deliver collector ID and work folder to
  processors.
- `coordinator:process_request`: ask processors to process a work folder or
  specific file.
- `coordinator:action_request`: deliver an action request to actors.
- `debug:registry_updated`: publish registered module state to debug clients.
- `debug:processor_result`: publish processor output to debug clients.
- `debug:actor_result`: publish actor output to debug clients.

## HTTP Debug Surface

```text
GET /debug/state
```

Returns the registered collectors, processors, and actors.
