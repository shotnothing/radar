// ======================================================
// Core Concept
// ======================================================
//
// Collector -> Observation -> NormalizedAction -> PredictedNextAction
//           -> UserSuggestion -> UserFeedback
//
// Collector decides WHEN to capture using Anchors.
// Processor decides WHAT is useful, normalizes noisy input,
// predicts the likely next action, and learns from user feedback.
//
// Example:
// - Every 5s
// - User clicked
// - URL changed
// - SeaTalk message received
// - Active app changed
// - File modified
//
// Each capture produces an Observation.
// Each useful observation can produce one or more NormalizedActions.
//
// ======================================================

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type ISODateTimeString = string;

// 0.0 to 1.0.
export type ConfidenceScore = number;

// Higher values make an action more likely to be suggested again.
export type PriorityScore = number;

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

  lastSuccessfulCollectionAt?: ISODateTimeString;
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
  observedAt: ISODateTimeString;

  eventTime?: ISODateTimeString;

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
    | "closed"
    | "searched"
    | "submitted"
    | "selected"
    | "dismissed"
    | "edited";

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

// ======================================================
// Privacy
// ======================================================

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

// ======================================================
// Quality
// ======================================================

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

// ======================================================
// Normalized Action
// ======================================================
//
// Observations are raw and source-specific. NormalizedActions
// are stable events that fuzzy matching and prediction can use.
//
// ======================================================

export type ActionApp =
  | "seatalk"
  | "browser"
  | "macos"
  | "filesystem"
  | "other";

export type NormalizedActionType =
  | "view"
  | "focus"
  | "click"
  | "type"
  | "send_message"
  | "receive_message"
  | "open_url"
  | "search"
  | "copy"
  | "paste"
  | "select"
  | "submit"
  | "open_file"
  | "close"
  | "unknown";

export interface NormalizedAction {
  id: string;

  observationId: string;

  sequenceId?: string;

  app: ActionApp;

  source: SourceInfo;

  type: NormalizedActionType;

  target?: ActionTarget;

  input?: ActionInput;

  occurredAt: ISODateTimeString;

  confidence: ConfidenceScore;

  privacy: PrivacyInfo;

  quality?: QualityInfo;
}

export interface ActionTarget {
  app?: string;

  bundleId?: string;

  windowTitle?: string;

  url?: string;

  domain?: string;

  route?: string;

  conversationId?: string;

  threadId?: string;

  filePath?: string;

  elementRole?: string;

  elementTitle?: string;

  // Stable path from AXTree, DOM, or app-specific hierarchy.
  elementPath?: string;

  entityId?: string;

  metadata?: Record<string, JsonValue>;
}

export interface ActionInput {
  text?: string;

  redactedText?: string;

  textHash?: string;

  selectedText?: string;

  language?: string;

  metadata?: Record<string, JsonValue>;
}

// ======================================================
// Action Sequence
// ======================================================

export interface ActionSequence {
  id: string;

  userId?: string;

  sessionId?: string;

  startedAt: ISODateTimeString;

  updatedAt: ISODateTimeString;

  actions: NormalizedActionRef[];

  context?: {
    activeApp?: string;

    activeWindowTitle?: string;

    activeUrl?: string;

    metadata?: Record<string, JsonValue>;
  };
}

export interface NormalizedActionRef {
  actionId: string;

  occurredAt: ISODateTimeString;

  app: ActionApp;

  type: NormalizedActionType;

  targetSummary?: string;
}

// ======================================================
// Prediction
// ======================================================

export interface PredictionProcessor {
  id: string;

  normalize(
    observation: Observation
  ): Promise<NormalizedAction[]>;

  predict(
    request: NextActionPredictionRequest
  ): Promise<PredictionSet>;

  createSuggestion(
    predictionSet: PredictionSet,
    options?: SuggestionOptions
  ): Promise<UserSuggestion>;

  recordFeedback(
    feedback: UserFeedback
  ): Promise<void>;
}

export interface NextActionPredictionRequest {
  id: string;

  sequenceId?: string;

  currentActionId?: string;

  observations?: Observation[];

  actions?: NormalizedAction[];

  maxCandidates?: number;

  locale?: string;

  createdAt: ISODateTimeString;

  privacy?: PrivacyInfo;
}

export interface PredictionSet {
  id: string;

  requestId: string;

  candidates: PredictedNextAction[];

  status: "ready" | "empty" | "low_confidence" | "error";

  errorMessage?: string;

  createdAt: ISODateTimeString;
}

export interface PredictedNextAction {
  id: string;

  requestId?: string;

  basedOnActionIds: string[];

  action: ExpectedAction;

  score: ConfidenceScore;

  priority: PriorityScore;

  reason?: string;

  evidence?: PredictionEvidence;

  model?: ModelInfo;

  expiresAt?: ISODateTimeString;

  createdAt: ISODateTimeString;
}

export interface ExpectedAction {
  app?: ActionApp;

  type: NormalizedActionType;

  target?: ActionTarget;

  input?: ActionInput;

  command?: {
    name: string;

    parameters?: Record<string, JsonValue>;
  };
}

export interface PredictionEvidence {
  fuzzySimilarity?: ConfidenceScore;

  historicalSelectionCount?: number;

  historicalDismissalCount?: number;

  recentFrequency?: number;

  matchedPatternIds?: string[];

  matchedActionIds?: string[];

  featureWeights?: Record<string, number>;
}

export interface ModelInfo {
  name: string;

  version?: string;

  provider?: string;
}

// ======================================================
// User Suggestion
// ======================================================

export interface SuggestionOptions {
  locale?: string;

  tone?: "concise" | "friendly" | "technical";

  maxChoices?: number;
}

export interface UserSuggestion {
  id: string;

  predictionSetId: string;

  predictions: PredictedNextAction[];

  // English text generated for the user, for example:
  // "Looks like you may want to send this message in SeaTalk."
  englishText: string;

  delivery?: {
    channel:
      | "desktop_notification"
      | "inline_overlay"
      | "chat_message"
      | "api";

    destination?: string;
  };

  status:
    | "created"
    | "shown"
    | "selected"
    | "dismissed"
    | "expired";

  createdAt: ISODateTimeString;

  shownAt?: ISODateTimeString;

  expiresAt?: ISODateTimeString;
}

// ======================================================
// User Feedback and Learning
// ======================================================

export interface UserFeedback {
  id: string;

  suggestionId: string;

  predictionId: string;

  feedback:
    | "selected"
    | "dismissed"
    | "wrong"
    | "edited";

  selectedActionId?: string;

  editedAction?: Partial<ExpectedAction>;

  priorityDelta?: number;

  occurredAt: ISODateTimeString;

  metadata?: Record<string, JsonValue>;
}

export interface LearnedActionPattern {
  id: string;

  signature: string;

  action: ExpectedAction;

  priority: PriorityScore;

  selectionCount: number;

  dismissalCount: number;

  lastSelectedAt?: ISODateTimeString;

  lastDismissedAt?: ISODateTimeString;

  sourcePredictionIds?: string[];

  metadata?: Record<string, JsonValue>;
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

    path?: string;
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
