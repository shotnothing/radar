// ======================================================
// Core Concept
// ======================================================
//
// Collector -> Observation
//
// Collector decides WHEN to capture using Anchors.
// Processor decides WHAT is useful.
//
// Example:
// - Every 5s
// - User clicked
// - URL changed
// - Telegram message received
// - Active app changed
// - File modified
//
// Each capture produces an Observation.
//
// ======================================================

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

// ======================================================
// Collector
// ======================================================

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

  lastSuccessfulCollectionAt?: string;
}

// ======================================================
// Anchor
// ======================================================

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

  occurredAt: string;

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

// ======================================================
// Observation
// ======================================================

export interface Observation {
  id: string;

  collectorId: string;

  source: SourceInfo;

  time: TimeInfo;

  // WHY this observation was captured
  anchor: Anchor;

  // WHAT was captured
  subject: SubjectInfo;

  content: ContentInfo;

  context?: ContextInfo;

  artifacts?: ArtifactRef[];

  // source-specific payload
  extraData?: Record<string, JsonValue>;

  privacy: PrivacyInfo;

  quality: QualityInfo;
}

// ======================================================
// Source
// ======================================================

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

// ======================================================
// Time
// ======================================================

export interface TimeInfo {
  observedAt: string;

  eventTime?: string;

  durationMs?: number;
}

// ======================================================
// Subject
// ======================================================

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

// ======================================================
// Content
// ======================================================

export interface ContentInfo {
  text?: string;

  markdown?: string;

  html?: string;

  metadata?: Record<string, JsonValue>;
}

// ======================================================
// Context
// ======================================================

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
    | "closed";

  nearbyEvents?: string[];
}

// ======================================================
// Artifacts
// ======================================================

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

  createdAt?: string;

  modifiedAt?: string;

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

// ======================================================
// Privacy
// ======================================================

export interface PrivacyInfo {
  sensitivity: "low" | "medium" | "high";

  containsPII?: boolean;

  redacted?: boolean;

  permissionScope: string[];
}

// ======================================================
// Quality
// ======================================================

export interface QualityInfo {
  confidence: number;

  completeness: "partial" | "full";

  extractionMethod:
    | "api"
    | "accessibility"
    | "browser_extension"
    | "filesystem"
    | "ocr";
}

// ======================================================
// Optional: macOS AXTree payload
// ======================================================

export interface AXTreeSnapshot {
  app: string;

  bundleId?: string;

  windowTitle?: string;

  selectedText?: string;

  visibleTexts: string[];

  focusedElement?: {
    role: string;

    title?: string;

    value?: string;
  };

  tree?: AXNode;
}

export interface AXNode {
  id: string;

  role: string;

  subrole?: string;

  title?: string;

  value?: string;

  description?: string;

  enabled?: boolean;

  focused?: boolean;

  selected?: boolean;

  frame?: {
    x: number;
    y: number;
    width: number;
    height: number;
  };

  children?: AXNode[];
}