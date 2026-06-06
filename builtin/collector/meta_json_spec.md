# Collector meta.json Spec

This spec defines the `meta.json` file used to identify, discover, configure,
and run each collector independently.

Each collector implementation should live in its own directory and include a
`meta.json` file at the collector root.

```text
builtin/collector/<collector-id>/
  meta.json
  ...
```

The engine uses `meta.json` for collector discovery and lifecycle management.
The collector implementation still owns capture behavior and must emit
`Observation` values as defined in `spec.md`.

## Goals

- Give every collector a stable ID and version.
- Describe what the collector captures and which permissions it needs.
- Define how the engine can run the collector by itself.
- Keep default runtime configuration close to the collector.
- Allow desktop and CLI surfaces to show collector status and controls.

## Required Behavior

- `id` must be globally stable and unique within the installed collector set.
- `runtime.command` must be runnable from `runtime.workingDirectory`.
- Relative paths are resolved from the directory containing `meta.json`.
- The engine may run one collector by ID without starting other collectors.
- Secrets must not be stored in `meta.json`; reference environment variable
  names instead.
- Default config must be safe: minimal capture, privacy-aware, and disableable.

## File Shape

```ts
export interface CollectorMeta {
  schemaVersion: "collector.meta/v1";

  id: string;

  name: string;

  version: string;

  description?: string;

  status?: "experimental" | "stable" | "deprecated";

  owner?: {
    team?: string;
    contact?: string;
  };

  runtime: CollectorRuntime;

  capabilities: CollectorMetaCapability[];

  permissions?: CollectorPermission[];

  anchors?: CollectorAnchorDeclaration[];

  emits: CollectorEmissionDeclaration;

  defaultConfig?: CollectorDefaultConfig;

  privacy?: CollectorPrivacyDeclaration;

  healthCheck?: CollectorHealthCheck;

  tags?: string[];
}

export interface CollectorRuntime {
  type: "python" | "node" | "binary" | "shell";

  workingDirectory?: string;

  command: string;

  args?: string[];

  env?: Record<string, string>;

  supports?: {
    start?: boolean;
    stop?: boolean;
    health?: boolean;
    once?: boolean;
  };
}

export interface CollectorMetaCapability {
  name: string;

  description?: string;
}

export interface CollectorPermission {
  id: string;

  name: string;

  required: boolean;

  reason: string;

  platform?: "macos" | "browser" | "engine" | "external";
}

export interface CollectorAnchorDeclaration {
  type:
    | "time_interval"
    | "user_action"
    | "system_event"
    | "app_event"
    | "content_change"
    | "state_change"
    | "manual"
    | "processor_request";

  name: string;

  defaultEnabled: boolean;

  intervalMs?: number;

  debounceMs?: number;

  throttleMs?: number;
}

export interface CollectorEmissionDeclaration {
  observationSourceTypes: Array<
    | "macos"
    | "browser"
    | "chat"
    | "filesystem"
    | "email"
    | "calendar"
    | "clipboard"
    | "notification"
  >;

  subjectKinds: Array<
    | "window"
    | "tab"
    | "message"
    | "conversation"
    | "file"
    | "notification"
    | "clipboard"
    | "input"
    | "selection"
  >;

  extraDataSchemas?: Record<string, string>;
}

export interface CollectorDefaultConfig {
  enabled: boolean;

  anchors?: Array<{
    name: string;
    enabled: boolean;
  }>;

  filters?: {
    includeApps?: string[];
    excludeApps?: string[];
    includeDomains?: string[];
    excludeDomains?: string[];
  };

  privacy?: {
    redactPII?: boolean;
    storeRaw?: boolean;
    retentionDays?: number;
  };
}

export interface CollectorPrivacyDeclaration {
  sensitivity: "low" | "medium" | "high";

  storesRawContent: boolean;

  defaultRetentionDays?: number;

  redactionStrategies?: Array<"none" | "mask" | "hash" | "drop" | "summarize">;

  risks?: string[];
}

export interface CollectorHealthCheck {
  command?: string;

  args?: string[];

  intervalMs?: number;

  timeoutMs?: number;
}
```

## Example

```json
{
  "schemaVersion": "collector.meta/v1",
  "id": "builtin.macos.active-window",
  "name": "macOS Active Window",
  "version": "0.1.0",
  "description": "Captures active macOS app and window state.",
  "status": "experimental",
  "runtime": {
    "type": "python",
    "workingDirectory": ".",
    "command": "python",
    "args": ["collector.py", "run"],
    "supports": {
      "start": true,
      "stop": true,
      "health": true,
      "once": true
    }
  },
  "capabilities": [
    {
      "name": "active_window",
      "description": "Reports the foreground app and window title."
    }
  ],
  "permissions": [
    {
      "id": "macos.accessibility",
      "name": "macOS Accessibility",
      "required": true,
      "reason": "Needed to read active window metadata.",
      "platform": "macos"
    }
  ],
  "anchors": [
    {
      "type": "state_change",
      "name": "active_window_changed",
      "defaultEnabled": true,
      "debounceMs": 250
    },
    {
      "type": "time_interval",
      "name": "active_window_poll",
      "defaultEnabled": false,
      "intervalMs": 5000
    }
  ],
  "emits": {
    "observationSourceTypes": ["macos"],
    "subjectKinds": ["window"],
    "extraDataSchemas": {
      "axtree": "../spec.md#macos-axtree-payload"
    }
  },
  "defaultConfig": {
    "enabled": false,
    "anchors": [
      {
        "name": "active_window_changed",
        "enabled": true
      }
    ],
    "privacy": {
      "redactPII": true,
      "storeRaw": false,
      "retentionDays": 7
    }
  },
  "privacy": {
    "sensitivity": "medium",
    "storesRawContent": false,
    "defaultRetentionDays": 7,
    "redactionStrategies": ["drop", "summarize"],
    "risks": ["Window titles can contain private document or conversation names."]
  },
  "healthCheck": {
    "command": "python",
    "args": ["collector.py", "health"],
    "intervalMs": 30000,
    "timeoutMs": 3000
  },
  "tags": ["builtin", "macos"]
}
```

## Running Collectors Individually

The engine should support a command equivalent to:

```bash
radar collector run builtin.macos.active-window
```

Runtime resolution should:

1. Discover all installed `meta.json` files.
2. Select the entry whose `id` matches the requested collector ID.
3. Merge `defaultConfig` with user or policy overrides.
4. Start `runtime.command` with `runtime.args` from `runtime.workingDirectory`.
5. Stream emitted observations into the engine event sink.

For one-shot debug collection, the engine may use `runtime.supports.once` and a
command equivalent to:

```bash
radar collector run builtin.macos.active-window --once
```

## Compatibility

Changes to `meta.json` must be versioned with `schemaVersion`. Add optional
fields for backward-compatible changes. Introduce a new schema version for
required field changes, behavior changes, or renamed fields.
