# Collected Data Interface

This document defines the things a collector can emit.

A collector emits raw `Observation` objects. Observations preserve source,
subject, content, artifacts, privacy metadata, and extraction quality. They do
not describe predicted next actions.

## Observation

```ts
export interface Observation {
  id: string;

  collectorId: string;

  source: SourceInfo;

  time: TimeInfo;

  // WHY this observation was captured.
  anchor: Anchor;

  // WHAT was captured.
  subject: SubjectInfo;

  content: ContentInfo;

  context?: ContextInfo;

  artifacts?: ArtifactRef[];

  // Source-specific payload, for example AXTree or browser extension data.
  extraData?: Record<string, JsonValue>;

  privacy: PrivacyInfo;

  quality: QualityInfo;
}
```

## Source and Time

```ts
export interface SourceInfo {
  type:
    | "macos"
    | "browser"
    | "chat"
    | "filesystem"
    | "email"
    | "calendar"
    | "clipboard"
    | "notification";

  app?: string;

  bundleId?: string;

  accountId?: string;

  workspaceId?: string;
}

export interface TimeInfo {
  observedAt: ISODateTimeString;

  eventTime?: ISODateTimeString;

  durationMs?: number;
}
```

## Subject and Content

```ts
export interface SubjectInfo {
  kind:
    | "window"
    | "tab"
    | "message"
    | "conversation"
    | "file"
    | "notification"
    | "clipboard"
    | "input"
    | "selection";

  title?: string;

  url?: string;

  filePath?: string;

  conversationId?: string;

  threadId?: string;
}

export interface ContentInfo {
  text?: string;

  markdown?: string;

  html?: string;

  metadata?: Record<string, JsonValue>;
}
```

## Context

```ts
export interface ContextInfo {
  activeApp?: string;

  activeWindowTitle?: string;

  userAction?:
    | "viewed"
    | "focused"
    | "typed"
    | "clicked"
    | "copied"
    | "pasted"
    | "sent"
    | "received"
    | "opened"
    | "closed"
    | "searched"
    | "submitted"
    | "selected"
    | "dismissed"
    | "edited";

  nearbyEvents?: string[];
}
```

## Artifacts

Artifacts hold large or binary captured content by reference. Screenshots should
use `kind: "screenshot"` and a URI such as `vault://artifacts/artifact_1`.

```ts
export interface ArtifactRef {
  id: string;

  kind:
    | "image"
    | "video"
    | "audio"
    | "pdf"
    | "file"
    | "screenshot";

  uri: string;

  originalPath?: string;

  mimeType?: string;

  sizeBytes?: number;

  hash?: string;

  createdAt?: ISODateTimeString;

  modifiedAt?: ISODateTimeString;

  extracted?: {
    text?: string;

    thumbnailUri?: string;

    dimensions?: {
      width: number;
      height: number;
    };

    durationMs?: number;
  };

  access?: {
    mode: "reference" | "copy" | "temporary";

    permissionScope?: string[];
  };
}
```

## Privacy and Quality

```ts
export interface PrivacyInfo {
  sensitivity: "low" | "medium" | "high";

  containsPII?: boolean;

  redacted?: boolean;

  rawContentStored?: boolean;

  redactionStrategy?:
    | "none"
    | "mask"
    | "hash"
    | "drop"
    | "summarize";

  retentionDays?: number;

  allowedUses?: Array<
    | "collection"
    | "normalization"
    | "prediction"
    | "user_feedback_learning"
    | "debugging"
    | "training"
  >;

  permissionScope: string[];
}

export interface QualityInfo {
  confidence: ConfidenceScore;

  completeness: "partial" | "full";

  extractionMethod:
    | "api"
    | "accessibility"
    | "browser_extension"
    | "filesystem"
    | "ocr";
}
```
