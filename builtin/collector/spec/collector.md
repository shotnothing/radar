# Collector Spec

A collector is a standalone process that observes one source of user activity and
writes collected data into a coordinator-assigned work folder.

## Responsibilities

- The collector owns data collection and artifact capture for its source.
- The collector connects to the coordinator Socket.IO server.
- The collector registers itself and sends health updates to the coordinator.
- The collector receives a `work_dir` from the coordinator during registration.
- The collector writes collected JSONL files and artifacts under `work_dir`.
- Each collector must provide a `meta.json` file so the coordinator can
  discover, launch, and register it.

## Registration

`meta.json` should describe the collector identity, supported source, runtime
command, required permissions, capabilities, emitted observation shapes, and
default config. Field names in `meta.json` should use snake_case.

After opening a Socket.IO connection, a collector must emit
`collector:register`.

```json
{
    "collector_id": "macos.axtree",
    "protocol_version": 1,
    "capabilities": ["active_window", "axtree", "screenshot"],
    "metadata": {
        "display_name": "macOS Accessibility Tree"
    }
}
```

The coordinator acknowledges registration with:

```json
{
    "ok": true,
    "role": "collector",
    "collector_id": "macos.axtree",
    "work_dir": "/Users/example/radar/debug/work/collectors/macos_axtree"
}
```

The collector owns all writes under `work_dir`. Socket.IO is used for
registration, health, configuration, and control messages, not for streaming
collected data.

## Socket.IO Events

Collectors emit these events to the coordinator:

- `collector:register`: register collector identity and capabilities.
- `collector:heartbeat`: report liveness, permission state, and current status.

The coordinator may emit these events to collectors:

- `coordinator:collector_config`: send updated config for a collector.
- `coordinator:collector_pause`: pause collection for a source or app.
- `coordinator:collector_resume`: resume collection after a pause.

Collectors should treat the coordinator as the only runtime router. They should
not directly invoke processors or actors.
