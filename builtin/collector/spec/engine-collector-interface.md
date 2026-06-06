# Engine Collector Interface

This document defines the boundary between the engine and a collector.

The engine owns collector lifecycle. A collector owns source-specific capture and
emits observations into the engine-provided event sink.

## Engine Responsibilities

- Enable or disable collectors.
- Pass anchor, filter, permission, and privacy configuration.
- Receive observations through `EventSink`.
- Monitor collector health.
- Stop collectors during shutdown or permission changes.

## Collector Responsibilities

- Report its capabilities.
- Start and stop cleanly.
- Capture only when configured anchors fire.
- Apply app, domain, and privacy filters before emitting data.
- Emit `Observation` objects, not predictions.

## Collector Contract

```ts
export interface Collector {
  id: string;

  capabilities(): CollectorCapability[];

  start(
    config: CollectorConfig,
    sink: EventSink
  ): Promise<void>;

  stop(): Promise<void>;

  health(): CollectorHealth;
}

export interface EventSink {
  emit(observation: Observation): Promise<void>;

  emitBatch(
    observations: Observation[]
  ): Promise<void>;
}

export interface CollectorConfig {
  enabled: boolean;

  anchors?: AnchorConfig[];

  permissions?: string[];

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

export interface CollectorCapability {
  name: string;
  description?: string;
}

export interface CollectorHealth {
  status: "healthy" | "degraded" | "unhealthy";

  message?: string;

  lastSuccessfulCollectionAt?: ISODateTimeString;
}
```

## Capture Anchors

An anchor explains why a capture happened. Common anchors include:

- Fixed interval capture, for example every 5 seconds.
- User clicked, typed, copied, pasted, or submitted something.
- Browser URL changed.
- SeaTalk message received.
- Active app or active window changed.
- File or clipboard content changed.
- Processor requested another snapshot.

```ts
export interface Anchor {
  id: string;

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

  occurredAt: ISODateTimeString;

  target?: {
    app?: string;
    bundleId?: string;

    windowTitle?: string;

    url?: string;

    filePath?: string;

    elementRole?: string;
    elementTitle?: string;
  };

  metadata?: Record<string, JsonValue>;
}

export interface AnchorConfig {
  type: Anchor["type"];

  name?: string;

  enabled: boolean;

  intervalMs?: number;

  debounceMs?: number;

  throttleMs?: number;

  filters?: {
    apps?: string[];
    bundleIds?: string[];
    domains?: string[];
    eventNames?: string[];
  };
}
```
