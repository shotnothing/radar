import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { io } from "socket.io-client";
import {
  Bell,
  Bot,
  CalendarCheck2,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Chrome,
  ClipboardCheck,
  Cpu,
  DatabaseZap,
  FileText,
  GitCommitHorizontal,
  GitPullRequest,
  Link2,
  Layers2,
  Loader2,
  Mail,
  MessageCircle,
  Play,
  RefreshCw,
  Send,
  Settings2,
  Sparkles,
  Square,
  Unplug,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";

function App() {
  const windowLabel = useMemo(() => getCurrentWindow().label, []);

  if (windowLabel === "settings") {
    return <SettingsWindow />;
  }

  return <AssistantWindow />;
}

type Suggestion = {
  id: number | string;
  title: string;
  body: string;
  primary_action: string;
  actor_id?: string;
  trigger_id?: string;
};

type ActorRunResponse = {
  ok: boolean;
  output?: {
    success?: boolean;
    message?: string;
    action?: {
      commit_message?: string;
      repo_path?: string;
      branch?: string;
    };
  };
  error?: string;
};

type AssistantRunState = {
  status: "idle" | "running" | "result" | "error";
  message: string;
};

type PredictionPattern = {
  items?: unknown[];
  support?: number;
  decayed_count?: number;
};

type ProcessorPredictionEvent = {
  id?: string;
  prediction_id?: string;
  confidence?: number;
  should_show?: boolean;
  transactions_seen?: number;
  patterns?: PredictionPattern[];
  suggested_action?: {
    title?: string;
    body?: string;
    action_text?: string;
    primary_action?: string;
  };
  llm?: {
    enabled?: boolean;
    status?: string;
    review?: {
      makes_sense?: boolean;
      should_show?: boolean;
      confidence?: number;
      reason?: string;
    };
    error?: string;
  };
  payload?: {
    confidence?: number;
    transactions_seen?: number;
    patterns?: PredictionPattern[];
  };
};

type SettingsSection =
  | "collector"
  | "processor"
  | "actor"
  | "connection"
  | "debug";

type ModuleConfig = {
  id: string;
  name: string;
  description: string;
  status: string;
  icon: LucideIcon;
  defaultEnabled: boolean;
};

type GoogleConnectionStatus = {
  connected: boolean;
  email: string | null;
  scopes: string[];
  configured: boolean;
};

type MonitoringStatus = {
  running: boolean;
  api_url: string;
  processor_url: string;
  chrome_bridge_url: string;
  work_dir: string;
};

type CollectorEvent = {
  id: string;
  collectorId: string;
  observedAt: number;
  sourceType: string;
  sourceApp: string;
  subjectKind: string;
  title: string;
  text: string;
  anchorType: string;
  action: string;
  contextApp: string;
  contextWindow: string;
  artifactCount: number;
  provenanceType: string;
  sourceUri: string;
  filePath: string;
  lineNumber: number;
};

type CollectorParseError = {
  filePath: string;
  lineNumber: number;
  message: string;
};

type CollectorEventsPayload = {
  dataRoot: string;
  totalEvents: number;
  returnedEvents: number;
  collectors: string[];
  sources: string[];
  errors: CollectorParseError[];
  events: CollectorEvent[];
};

type TimelineRun = {
  collectorId: string;
  startAt: number;
  endAt: number;
  events: CollectorEvent[];
};

const PROCESSOR_APP_URL =
  import.meta.env.VITE_RADAR_PROCESSOR_APP_URL || "http://127.0.0.1:5060";

const settingsTabs: Array<{
  id: SettingsSection;
  label: string;
  icon: LucideIcon;
}> = [
  { id: "collector", label: "Collector", icon: Settings2 },
  { id: "processor", label: "Processor", icon: Cpu },
  { id: "actor", label: "Actor", icon: Bot },
  { id: "connection", label: "Connection", icon: Link2 },
  { id: "debug", label: "Debug", icon: DatabaseZap },
];

const settingsCopy: Record<
  SettingsSection,
  { title: string; description: string; empty: string }
> = {
  collector: {
    title: "Collector",
    description: "Local sources that collect activity signals.",
    empty: "No collectors configured.",
  },
  processor: {
    title: "Processor",
    description: "Pipelines that turn collected signals into intent.",
    empty: "No processors configured.",
  },
  actor: {
    title: "Actor",
    description: "Actions that can be triggered after intent is detected.",
    empty: "No actors configured yet.",
  },
  connection: {
    title: "Connection",
    description: "External accounts Radar can use with your permission.",
    empty: "No connections configured yet.",
  },
  debug: {
    title: "Debug Viewer",
    description: "Collected event timeline from the local Radar data folder.",
    empty: "No collected events yet.",
  },
};

const moduleCatalog: Record<SettingsSection, ModuleConfig[]> = {
  collector: [
    {
      id: "collector.chrome",
      name: "Chrome",
      description: "Browser activity, page context, and active tab changes.",
      status: "chrome.browser",
      icon: Chrome,
      defaultEnabled: true,
    },
    {
      id: "collector.seatalk",
      name: "SeaTalk",
      description: "Conversation activity and message context.",
      status: "seatalk.chat",
      icon: MessageCircle,
      defaultEnabled: false,
    },
    {
      id: "collector.chat_transcript",
      name: "Chat Transcript",
      description: "Local Codex and Claude transcript files.",
      status: "chat.transcript",
      icon: FileText,
      defaultEnabled: true,
    },
    {
      id: "collector.macos",
      name: "macOS Activity",
      description: "Local clicks, submissions, active window, and focused element context.",
      status: "macos.activity",
      icon: Workflow,
      defaultEnabled: true,
    },
  ],
  processor: [
    {
      id: "processor.intent",
      name: "Intent Analyzer",
      description: "Detects repeated actions and likely next steps.",
      status: "intent.analyzer",
      icon: Workflow,
      defaultEnabled: true,
    },
    {
      id: "processor.context",
      name: "Context Summarizer",
      description: "Builds compact summaries from recent collected events.",
      status: "context.summarizer",
      icon: FileText,
      defaultEnabled: true,
    },
    {
      id: "processor.recommendation",
      name: "Recommendation Ranker",
      description: "Scores suggestions before they reach the popup.",
      status: "recommendation.ranker",
      icon: Bell,
      defaultEnabled: false,
    },
  ],
  actor: [
    {
      id: "actor.youtube_search_nab",
      name: "YouTube Search kpop",
      description: "Offers to search kpop on YouTube.",
      status: "builtin.youtube_search_nab",
      icon: Workflow,
      defaultEnabled: true,
    },
    {
      id: "actor.gmail_followup_draft",
      name: "Gmail Follow-up Draft",
      description: "Opens Compose and inserts a short follow-up draft.",
      status: "builtin.gmail_followup_draft",
      icon: Mail,
      defaultEnabled: true,
    },
    {
      id: "actor.gmail_reply_email",
      name: "Gmail Reply Email",
      description: "Uses Codex CLI to open the reply editor for Gmail threads.",
      status: "builtin.gmail_reply_email",
      icon: Mail,
      defaultEnabled: true,
    },
    {
      id: "actor.calendar_next_open_timeslot",
      name: "Calendar Open Timeslot",
      description: "Shows a placeholder timeslot action for Google Calendar week view.",
      status: "builtin.calendar_next_open_timeslot",
      icon: CalendarDays,
      defaultEnabled: true,
    },
    {
      id: "actor.codex_use_radar_skill",
      name: "Codex Radar Skill",
      description: "Offers Radar Coding Memory when Codex is open on a known repo.",
      status: "builtin.codex_use_radar_skill",
      icon: Bot,
      defaultEnabled: true,
    },
    {
      id: "actor.git_commit_message",
      name: "Git Commit Message",
      description: "Generates and copies a commit message when a terminal is at git commit.",
      status: "builtin.git_commit_message",
      icon: GitCommitHorizontal,
      defaultEnabled: true,
    },
    {
      id: "actor.meeting_brief",
      name: "Meeting Brief",
      description: "Prepares agenda, attendees, and recent context before a calendar event.",
      status: "demo.meeting_brief",
      icon: CalendarCheck2,
      defaultEnabled: false,
    },
    {
      id: "actor.gmail_follow_up",
      name: "Gmail Follow-up",
      description: "Drafts a polite follow-up when an email thread is waiting on a reply.",
      status: "demo.gmail_follow_up",
      icon: Mail,
      defaultEnabled: false,
    },
    {
      id: "actor.seatalk_action_items",
      name: "SeaTalk Action Items",
      description: "Turns recent chat decisions into a concise task list or reminder draft.",
      status: "demo.seatalk_action_items",
      icon: ClipboardCheck,
      defaultEnabled: false,
    },
    {
      id: "actor.pr_review_prep",
      name: "PR Review Prep",
      description: "Summarizes changed files and opens a focused review checklist for a branch.",
      status: "demo.pr_review_prep",
      icon: GitPullRequest,
      defaultEnabled: false,
    },
    {
      id: "actor.send_status_update",
      name: "Status Update Sender",
      description: "Composes a short project update from recent work and sends it to chat.",
      status: "demo.status_update_sender",
      icon: Send,
      defaultEnabled: false,
    },
  ],
  connection: [],
  debug: [],
};

function getInitialModuleState() {
  return Object.fromEntries(
    Object.values(moduleCatalog)
      .flat()
      .map((item) => [item.id, item.defaultEnabled])
  );
}

function formatPercent(value: number | undefined) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "0%";
  }
  return `${Math.round(value * 100)}%`;
}

function compactPayloadLabel(value: unknown) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "unknown action";
  }
  return text.length > 120 ? `${text.slice(0, 117)}...` : text;
}

function patternLabel(pattern: PredictionPattern) {
  const items = pattern.items ?? [];
  if (items.length === 0) {
    return "unknown action";
  }
  return items.map(compactPayloadLabel).join(" -> ");
}

const debugPalette = [
  "#0f766e",
  "#b45309",
  "#2563eb",
  "#be123c",
  "#6d28d9",
  "#15803d",
  "#c2410c",
  "#0369a1",
];
const debugEventUnitPx = 18;
const debugGroupMinWidthPx = 112;
const monthNames = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function formatShortTime(ms: number) {
  if (!ms) {
    return "";
  }
  const date = new Date(ms);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatDebugDate(ms: number) {
  if (!ms) {
    return "Unknown time";
  }
  const date = new Date(ms);
  return `${date.getDate()} ${monthNames[date.getMonth()]} ${date.getFullYear()}, ${formatShortTime(ms)}`;
}

function formatDebugMarker(ms: number) {
  if (!ms) {
    return "Unknown time";
  }
  const date = new Date(ms);
  return `${date.getDate()} ${monthNames[date.getMonth()]} / ${pad(date.getHours())}:00`;
}

function debugMarkerKey(ms: number) {
  if (!ms) {
    return "unknown";
  }
  const date = new Date(ms);
  date.setMinutes(0, 0, 0);
  return String(date.getTime());
}

function formatValueList(values: string[]) {
  if (values.length === 0) {
    return "-";
  }
  if (values.length <= 2) {
    return values.join(", ");
  }
  return `${values.slice(0, 2).join(", ")} +${values.length - 2}`;
}

function uniqueEventValues(events: CollectorEvent[], key: keyof CollectorEvent) {
  return [
    ...new Set(
      events
        .map((event) => event[key])
        .filter((value): value is string => typeof value === "string" && value.length > 0)
    ),
  ];
}

function canMergeTimelineRun(run: TimelineRun, event: CollectorEvent) {
  return run.collectorId === event.collectorId;
}

function createTimelineRuns(events: CollectorEvent[]) {
  const runs: TimelineRun[] = [];

  for (const event of [...events].sort((a, b) => a.observedAt - b.observedAt)) {
    const lastRun = runs.at(-1);
    if (lastRun && canMergeTimelineRun(lastRun, event)) {
      lastRun.events.push(event);
      lastRun.endAt = event.observedAt || lastRun.endAt;
      continue;
    }

    runs.push({
      collectorId: event.collectorId,
      startAt: event.observedAt,
      endAt: event.observedAt,
      events: [event],
    });
  }

  return runs;
}

function formatRunTime(run: TimelineRun) {
  if (run.startAt === run.endAt) {
    return formatShortTime(run.startAt) || "Unknown";
  }
  return `${formatShortTime(run.startAt)}-${formatShortTime(run.endAt)}`;
}

function timelineRunLabel(run: TimelineRun) {
  const count = run.events.length;
  const range =
    run.startAt === run.endAt
      ? formatDebugDate(run.startAt)
      : `${formatDebugDate(run.startAt)} to ${formatDebugDate(run.endAt)}`;
  return `${count} ${count === 1 ? "event" : "events"} from ${run.collectorId}, ${range}`;
}

function processorPredictionToSuggestion(
  prediction: ProcessorPredictionEvent
): Suggestion | null {
  if (
    prediction.should_show === false ||
    prediction.llm?.review?.should_show === false
  ) {
    return null;
  }

  const suggestedAction = prediction.suggested_action;
  if (
    suggestedAction?.title ||
    suggestedAction?.body ||
    suggestedAction?.action_text
  ) {
    return {
      id:
        prediction.prediction_id ??
        prediction.id ??
        `processor-prediction-${Date.now()}`,
      title: suggestedAction.title?.trim() || "Suggested next action",
      body: suggestedAction.body?.trim() || "Radar found a likely next step.",
      primary_action:
        suggestedAction.primary_action?.trim() ||
        suggestedAction.action_text?.trim() ||
        "Open",
    };
  }

  const payload = prediction.payload ?? {};
  const confidence = prediction.confidence ?? payload.confidence ?? 0;
  const transactionsSeen =
    prediction.transactions_seen ?? payload.transactions_seen ?? 0;
  const patterns = prediction.patterns ?? payload.patterns ?? [];
  const patternLines = patterns.slice(0, 3).map((pattern, index) => {
    const support = formatPercent(pattern.support);
    const count =
      typeof pattern.decayed_count === "number"
        ? `, count ${pattern.decayed_count.toFixed(2)}`
        : "";
    return `${index + 1}. ${patternLabel(pattern)} (${support}${count})`;
  });

  const body = [
    `Confidence ${formatPercent(confidence)} from ${transactionsSeen.toLocaleString()} learned events.`,
    patternLines.length > 0
      ? `Top matches:\n\n${patternLines.join("\n")}`
      : "No pattern details were included.",
  ].join("\n\n");

  return {
    id:
      prediction.prediction_id ??
      prediction.id ??
      `processor-prediction-${Date.now()}`,
    title: "Suggested next action",
    body,
    primary_action: "Got it",
  };
}

function AssistantWindow() {
  const [currentSuggestion, setCurrentSuggestion] = useState<Suggestion | null>(
    null
  );
  const [queue, setQueue] = useState<Suggestion[]>([]);
  const [runState, setRunState] = useState<AssistantRunState>({
    status: "idle",
    message: "",
  });
  const currentSuggestionRef = useRef<Suggestion | null>(null);
  const queueRef = useRef<Suggestion[]>([]);

  async function enqueueSuggestion(suggestion: Suggestion) {
    const window = getCurrentWindow();

    if (currentSuggestionRef.current) {
      setQueue((pending) => {
        const nextQueue = [...pending, suggestion];
        queueRef.current = nextQueue;
        return nextQueue;
      });
    } else {
      activateSuggestion(suggestion);
    }

    await window.show();
  }

  useEffect(() => {
    const unlisten = listen<Suggestion>(
      "radar://mock-suggestion",
      async (event) => {
        await enqueueSuggestion(event.payload);
      }
    );

    return () => {
      void unlisten.then((dispose) => dispose());
    };
  }, []);

  useEffect(() => {
    const socket = io(PROCESSOR_APP_URL, {
      transports: ["websocket", "polling"],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 5000,
    });

    socket.on("prediction_generated", async (prediction: ProcessorPredictionEvent) => {
      const suggestion = processorPredictionToSuggestion(prediction);
      if (suggestion) {
        await enqueueSuggestion(suggestion);
      }
    });

    socket.on("connect_error", (error) => {
      console.error("Failed to connect to processor app:", error.message);
    });

    return () => {
      socket.disconnect();
    };
  }, []);

  function activateSuggestion(suggestion: Suggestion) {
    setRunState({ status: "idle", message: "" });
    currentSuggestionRef.current = suggestion;
    setCurrentSuggestion(suggestion);
  }

  async function dismissCurrent() {
    const suggestion = currentSuggestionRef.current;
    if (suggestion?.actor_id && suggestion.trigger_id) {
      try {
        await invoke("complete_actor_suggestion", {
          actorId: suggestion.actor_id,
          triggerId: suggestion.trigger_id,
          run: false,
        });
      } catch (error) {
        console.error("Failed to dismiss actor suggestion:", error);
      }
    }

    await advanceSuggestion();
  }

  async function runCurrentAction() {
    const suggestion = currentSuggestionRef.current;
    if (suggestion?.actor_id && suggestion.trigger_id) {
      setRunState({ status: "running", message: "" });
      try {
        const response = await invoke<ActorRunResponse>("complete_actor_suggestion", {
          actorId: suggestion.actor_id,
          triggerId: suggestion.trigger_id,
          run: true,
        });
        const commitMessage = response.output?.action?.commit_message;
        if (commitMessage) {
          setRunState({
            status: "result",
            message: `Commit message copied to clipboard:\n\n${commitMessage}`,
          });
          return;
        }
        await advanceSuggestion();
      } catch (error) {
        console.error("Failed to complete actor suggestion:", error);
        setRunState({
          status: "error",
          message: `Failed to run action: ${String(error)}`,
        });
      }
      return;
    }

    await advanceSuggestion();
  }

  async function advanceSuggestion() {
    const [nextSuggestion, ...remainingSuggestions] = queueRef.current;
    queueRef.current = remainingSuggestions;
    setQueue(remainingSuggestions);

    if (nextSuggestion) {
      activateSuggestion(nextSuggestion);
      return;
    }

    currentSuggestionRef.current = null;
    setCurrentSuggestion(null);
    setRunState({ status: "idle", message: "" });
    await invoke("dismiss_assistant_window");
  }

  if (!currentSuggestion) {
    return <Card className="assistant-window assistant-window--empty" />;
  }

  return (
    <Card className="assistant-window" role="main" aria-label="Radar suggestion">
      <div className="assistant-surface" key={currentSuggestion.id}>
        <header className="assistant-header" data-tauri-drag-region="">
          <div className="assistant-brand" data-tauri-drag-region="">
            <span
              className="assistant-mark"
              aria-hidden="true"
              data-tauri-drag-region=""
            >
              <Sparkles size={14} strokeWidth={2.2} />
            </span>
            <span data-tauri-drag-region="">Radar</span>
          </div>
          {queue.length > 0 ? (
            <span
              className="queue-badge"
              aria-label={`${queue.length} suggestions waiting`}
              data-tauri-drag-region=""
            >
              <Layers2 size={13} strokeWidth={2.3} />
              {queue.length}
            </span>
          ) : null}
        </header>

        <section className="message">
          <ReactMarkdown>
            {runState.status === "idle"
              ? `**${currentSuggestion.title}**\n\n${currentSuggestion.body}`
              : runState.status === "running"
                ? `**Generating commit message**\n\nAnalyzing the current Git diff...`
                : `**${runState.status === "error" ? "Action failed" : "Commit message ready"}**\n\n${runState.message}`}
          </ReactMarkdown>
        </section>

        <section className="controls" aria-label="Suggestion actions">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 rounded-[9px] px-2.5 text-xs text-slate-600 hover:bg-white/65 hover:text-slate-900"
            onClick={(event) => {
              event.stopPropagation();
              void dismissCurrent();
            }}
          >
            {runState.status === "result" || runState.status === "error" ? "Done" : "Dismiss"}
          </Button>
          {runState.status === "idle" || runState.status === "running" ? (
            <Button
              type="button"
              size="sm"
              disabled={runState.status === "running"}
              className="h-7 rounded-[9px] bg-slate-950 px-2.5 text-xs text-white shadow-sm hover:bg-slate-800"
              onClick={(event) => {
                event.stopPropagation();
                void runCurrentAction();
              }}
            >
              {runState.status === "running" ? (
                <>
                  <Loader2 size={13} className="animate-spin" />
                  Generating
                </>
              ) : (
                currentSuggestion.primary_action
              )}
            </Button>
          ) : null}
        </section>
      </div>
    </Card>
  );
}

function SettingsWindow() {
  const [activeSection, setActiveSection] =
    useState<SettingsSection>("collector");
  const [enabledModules, setEnabledModules] = useState<Record<string, boolean>>(
    getInitialModuleState
  );
  const [googleStatus, setGoogleStatus] =
    useState<GoogleConnectionStatus | null>(null);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [googleError, setGoogleError] = useState("");
  const [monitoringStatus, setMonitoringStatus] =
    useState<MonitoringStatus | null>(null);
  const [monitoringBusy, setMonitoringBusy] = useState(false);
  const [monitoringError, setMonitoringError] = useState("");

  useEffect(() => {
    const window = getCurrentWindow();
    const unlisten = window.onCloseRequested(async (event) => {
      event.preventDefault();
      await window.hide();
    });

    return () => {
      void unlisten.then((dispose) => dispose());
    };
  }, []);

  useEffect(() => {
    void refreshGoogleStatus();
  }, []);

  useEffect(() => {
    void refreshMonitoringStatus();

    const intervalId = globalThis.setInterval(() => {
      void refreshMonitoringStatus();
    }, 3000);

    return () => globalThis.clearInterval(intervalId);
  }, []);

  async function refreshMonitoringStatus() {
    try {
      const status = await invoke<MonitoringStatus>("get_monitoring_status");
      setMonitoringStatus(status);
      setMonitoringError("");
    } catch (error) {
      setMonitoringError(String(error));
    }
  }

  async function startMonitoring() {
    setMonitoringBusy(true);
    setMonitoringError("");

    try {
      const collectorIds = moduleCatalog.collector
        .filter((item) => enabledModules[item.id])
        .map((item) => item.id);
      const status = await invoke<MonitoringStatus>("start_monitoring", {
        collectorIds,
      });
      setMonitoringStatus(status);
    } catch (error) {
      setMonitoringError(String(error));
      await refreshMonitoringStatus();
    } finally {
      setMonitoringBusy(false);
    }
  }

  async function stopMonitoring() {
    setMonitoringBusy(true);
    setMonitoringError("");

    try {
      const status = await invoke<MonitoringStatus>("stop_monitoring");
      setMonitoringStatus(status);
    } catch (error) {
      setMonitoringError(String(error));
      await refreshMonitoringStatus();
    } finally {
      setMonitoringBusy(false);
    }
  }

  async function refreshGoogleStatus() {
    try {
      const status = await invoke<GoogleConnectionStatus>(
        "get_google_connection_status"
      );
      setGoogleStatus(status);
    } catch (error) {
      setGoogleError(String(error));
    }
  }

  async function connectGoogle() {
    setGoogleBusy(true);
    setGoogleError("");

    try {
      const status = await invoke<GoogleConnectionStatus>(
        "connect_google_account"
      );
      setGoogleStatus(status);
    } catch (error) {
      setGoogleError(String(error));
      await refreshGoogleStatus();
    } finally {
      setGoogleBusy(false);
    }
  }

  async function disconnectGoogle() {
    setGoogleBusy(true);
    setGoogleError("");

    try {
      const status = await invoke<GoogleConnectionStatus>(
        "disconnect_google_account"
      );
      setGoogleStatus(status);
    } catch (error) {
      setGoogleError(String(error));
    } finally {
      setGoogleBusy(false);
    }
  }

  const activeCopy = settingsCopy[activeSection];
  const activeItems = moduleCatalog[activeSection];
  const isConnectionSection = activeSection === "connection";
  const isDebugSection = activeSection === "debug";
  const monitoringRunning = monitoringStatus?.running ?? false;

  return (
    <Card className="settings-window" role="main" aria-label="Radar settings">
      <aside className="settings-sidebar" aria-label="Settings sections">
        <div className="settings-brand">
          <span className="settings-brand-mark" aria-hidden="true">
            <Sparkles size={14} strokeWidth={2.2} />
          </span>
          <span>Radar</span>
        </div>

        <nav className="settings-tabs">
          {settingsTabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = tab.id === activeSection;

            return (
              <Button
                key={tab.id}
                type="button"
                variant="ghost"
                aria-pressed={isActive}
                className={`settings-tab ${isActive ? "settings-tab--active" : ""}`}
                onClick={() => setActiveSection(tab.id)}
              >
                <Icon size={15} strokeWidth={2.2} />
                <span>{tab.label}</span>
              </Button>
            );
          })}
        </nav>
      </aside>

      <main className="settings-content">
        <header className="settings-content-header">
          <div>
            <h1>{activeCopy.title}</h1>
            <p>{activeCopy.description}</p>
          </div>
          {!isConnectionSection ? (
            <div className="monitoring-control" aria-label="Monitoring control">
              <div className="monitoring-copy">
                <span
                  className={`monitoring-status ${
                    monitoringRunning ? "monitoring-status--running" : ""
                  }`}
                >
                  {monitoringRunning ? "running" : "stopped"}
                </span>
                <span className="monitoring-endpoint">
                  {monitoringStatus?.api_url ?? "local runtime"}
                </span>
              </div>
              <Button
                type="button"
                variant={monitoringRunning ? "outline" : "default"}
                size="sm"
                className={`monitoring-button ${
                  monitoringRunning ? "" : "monitoring-button--primary"
                }`}
                disabled={monitoringBusy}
                onClick={() =>
                  monitoringRunning
                    ? void stopMonitoring()
                    : void startMonitoring()
                }
              >
                {monitoringBusy ? (
                  <Loader2
                    className="monitoring-spinner"
                    size={14}
                    strokeWidth={2.2}
                  />
                ) : monitoringRunning ? (
                  <Square size={13} strokeWidth={2.4} />
                ) : (
                  <Play size={14} strokeWidth={2.4} />
                )}
                {monitoringBusy
                  ? "Working"
                  : monitoringRunning
                    ? "Stop"
                    : "Start"}
              </Button>
              {monitoringError ? (
                <p className="monitoring-error">{monitoringError}</p>
              ) : null}
            </div>
          ) : null}
        </header>

        {isConnectionSection ? (
          <ConnectionSettings
            googleStatus={googleStatus}
            googleBusy={googleBusy}
            googleError={googleError}
            onConnectGoogle={() => void connectGoogle()}
            onDisconnectGoogle={() => void disconnectGoogle()}
          />
        ) : isDebugSection ? (
          <DebugViewer />
        ) : activeItems.length > 0 ? (
          <section className="module-list" aria-label={`${activeCopy.title} list`}>
            {activeItems.map((item) => {
              const Icon = item.icon;
              const enabled = enabledModules[item.id] ?? false;

              return (
                <article className="module-row" key={item.id}>
                  <div className="module-icon" aria-hidden="true">
                    <Icon size={17} strokeWidth={2.1} />
                  </div>
                  <div className="module-copy">
                    <div className="module-title-row">
                      <h2>{item.name}</h2>
                      <span className="module-status">{item.status}</span>
                    </div>
                    <p>{item.description}</p>
                  </div>
                  <Switch
                    checked={enabled}
                    onCheckedChange={(checked) =>
                      setEnabledModules((current) => ({
                        ...current,
                        [item.id]: checked,
                      }))
                    }
                    aria-label={`${enabled ? "Disable" : "Enable"} ${item.name}`}
                    className="module-switch"
                  />
                </article>
              );
            })}
          </section>
        ) : (
          <section className="settings-empty" aria-label="Empty settings section">
            <Bot size={22} strokeWidth={1.9} />
            <p>{activeCopy.empty}</p>
          </section>
        )}
      </main>
    </Card>
  );
}

function DebugViewer() {
  const [payload, setPayload] = useState<CollectorEventsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const timelineScrollRef = useRef<HTMLDivElement | null>(null);

  const colorMap = useMemo(() => {
    const map = new Map<string, string>();
    (payload?.collectors ?? []).forEach((collector, index) => {
      map.set(collector, debugPalette[index % debugPalette.length]);
    });
    return map;
  }, [payload?.collectors]);

  const timelineRuns = useMemo(
    () => createTimelineRuns(payload?.events ?? []),
    [payload?.events]
  );

  useEffect(() => {
    void loadEvents();
  }, []);

  useEffect(() => {
    updateScrollArrows();
  }, [timelineRuns.length]);

  async function loadEvents() {
    setLoading(true);
    setError("");

    try {
      const nextPayload = await invoke<CollectorEventsPayload>(
        "get_collector_events",
        { limit: 1000 }
      );
      setPayload(nextPayload);
      requestAnimationFrame(updateScrollArrows);
    } catch (loadError) {
      setError(String(loadError));
    } finally {
      setLoading(false);
    }
  }

  function updateScrollArrows() {
    const node = timelineScrollRef.current;
    if (!node) {
      setCanScrollLeft(false);
      setCanScrollRight(false);
      return;
    }

    const maxScrollLeft = node.scrollWidth - node.clientWidth;
    const canScroll = maxScrollLeft > 1;
    setCanScrollLeft(canScroll && node.scrollLeft > 1);
    setCanScrollRight(canScroll && node.scrollLeft < maxScrollLeft - 1);
  }

  function scrollTimeline(direction: -1 | 1) {
    const node = timelineScrollRef.current;
    if (!node) {
      return;
    }

    node.scrollBy({
      left: direction * Math.max(node.clientWidth * 0.8, 220),
      behavior: "smooth",
    });
    requestAnimationFrame(updateScrollArrows);
  }

  const totalEvents = payload?.totalEvents ?? 0;
  const collectorCount = payload?.collectors.length ?? 0;
  const parseErrorCount = payload?.errors.length ?? 0;
  const showEmpty = !loading && !error && timelineRuns.length === 0;

  return (
    <section className="debug-viewer" aria-label="Debug viewer">
      <div className="debug-toolbar">
        <div className="debug-legend" aria-label="Collector legend">
          {(payload?.collectors ?? []).map((collector) => (
            <span
              className="debug-legend-item"
              style={{ "--event-color": colorMap.get(collector) } as React.CSSProperties}
              key={collector}
            >
              <span className="debug-legend-swatch" aria-hidden="true" />
              <span>{collector}</span>
            </span>
          ))}
        </div>

        <Button
          type="button"
          size="sm"
          variant="outline"
          className="debug-refresh"
          disabled={loading}
          onClick={() => void loadEvents()}
          aria-label="Refresh collected events"
          title="Refresh collected events"
        >
          <RefreshCw
            size={14}
            strokeWidth={2.2}
            className={loading ? "monitoring-spinner" : ""}
          />
          {loading ? "Loading" : "Refresh"}
        </Button>
      </div>

      <div className="debug-summary" aria-label="Debug summary">
        <div>
          <span>{totalEvents.toLocaleString()}</span>
          <small>events</small>
        </div>
        <div>
          <span>{collectorCount.toLocaleString()}</span>
          <small>collectors</small>
        </div>
        <div>
          <span>{parseErrorCount.toLocaleString()}</span>
          <small>parse issues</small>
        </div>
      </div>

      {payload?.dataRoot ? (
        <p className="debug-data-root" title={payload.dataRoot}>
          {payload.dataRoot}
        </p>
      ) : null}

      {error ? (
        <section className="settings-empty debug-empty" aria-label="Debug load error">
          <DatabaseZap size={22} strokeWidth={1.9} />
          <p>{error}</p>
        </section>
      ) : showEmpty ? (
        <section className="settings-empty debug-empty" aria-label="No collected events">
          <DatabaseZap size={22} strokeWidth={1.9} />
          <p>No collected events yet.</p>
        </section>
      ) : (
        <section className="debug-timeline-shell" aria-label="Collected event timeline">
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            className="debug-scroll-button debug-scroll-button-left"
            hidden={!canScrollLeft}
            onClick={() => scrollTimeline(-1)}
            aria-label="Scroll timeline left"
            title="Scroll timeline left"
          >
            <ChevronLeft size={15} strokeWidth={2.3} />
          </Button>
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            className="debug-scroll-button debug-scroll-button-right"
            hidden={!canScrollRight}
            onClick={() => scrollTimeline(1)}
            aria-label="Scroll timeline right"
            title="Scroll timeline right"
          >
            <ChevronRight size={15} strokeWidth={2.3} />
          </Button>

          <div
            className="debug-timeline-scroll"
            ref={timelineScrollRef}
            onScroll={updateScrollArrows}
          >
            <ol className="debug-timeline">
              {timelineRuns.flatMap((run, index) => {
                const previousRun = timelineRuns[index - 1];
                const needsMarker =
                  !previousRun ||
                  debugMarkerKey(previousRun.startAt) !== debugMarkerKey(run.startAt);
                const nodes = [];

                if (needsMarker) {
                  nodes.push(
                    <li
                      className="debug-time-marker"
                      key={`marker-${debugMarkerKey(run.startAt)}-${index}`}
                    >
                      <time
                        dateTime={
                          run.startAt ? new Date(run.startAt).toISOString() : undefined
                        }
                      >
                        {formatDebugMarker(run.startAt)}
                      </time>
                    </li>
                  );
                }

                nodes.push(
                  <TimelineRunItem
                    key={`run-${run.collectorId}-${run.startAt}-${index}`}
                    run={run}
                    color={colorMap.get(run.collectorId) ?? debugPalette[0]}
                  />
                );

                return nodes;
              })}
            </ol>
          </div>
        </section>
      )}
    </section>
  );
}

function TimelineRunItem({ run, color }: { run: TimelineRun; color: string }) {
  const count = run.events.length;
  const segmentWidth = debugEventUnitPx * count;
  const source = formatValueList(uniqueEventValues(run.events, "sourceType"));
  const app = formatValueList(uniqueEventValues(run.events, "sourceApp"));
  const kind = [formatValueList(uniqueEventValues(run.events, "subjectKind")), formatRunTime(run)]
    .filter((value) => value && value !== "-")
    .join(" / ");

  return (
    <li
      className="debug-timeline-group"
      style={{ inlineSize: Math.max(segmentWidth, debugGroupMinWidthPx) }}
      aria-label={timelineRunLabel(run)}
    >
      <div
        className="debug-timeline-segment"
        style={
          {
            "--event-color": color,
            inlineSize: segmentWidth,
          } as React.CSSProperties
        }
        title={timelineRunLabel(run)}
      >
        {count > 1 ? <span>{count.toLocaleString()}</span> : null}
      </div>
      <div className="debug-segment-meta">
        <span>{source}</span>
        <span>{app}</span>
        <small>{kind}</small>
      </div>
    </li>
  );
}

function ConnectionSettings({
  googleStatus,
  googleBusy,
  googleError,
  onConnectGoogle,
  onDisconnectGoogle,
}: {
  googleStatus: GoogleConnectionStatus | null;
  googleBusy: boolean;
  googleError: string;
  onConnectGoogle: () => void;
  onDisconnectGoogle: () => void;
}) {
  const connected = googleStatus?.connected ?? false;
  const configured = googleStatus?.configured ?? false;
  const email = googleStatus?.email;
  const scopeLabel =
    googleStatus?.scopes.includes("https://www.googleapis.com/auth/gmail.readonly")
      ? "Gmail read-only"
      : "Google account";

  return (
    <section className="connection-list" aria-label="Connection list">
      <article className="connection-row">
        <div className="connection-icon" aria-hidden="true">
          <Mail size={18} strokeWidth={2.1} />
        </div>

        <div className="connection-copy">
          <div className="module-title-row">
            <h2>Google</h2>
            <span
              className={`connection-status ${
                connected ? "connection-status--connected" : ""
              }`}
            >
              {connected ? "connected" : "not connected"}
            </span>
          </div>
          <p>
            {connected && email
              ? email
              : "Link a Google account so Radar can read Gmail with your permission."}
          </p>
          <div className="connection-meta">
            <span>{scopeLabel}</span>
            <span>OAuth browser sign-in</span>
          </div>
          {!configured ? (
            <p className="connection-warning">
              Add a Google OAuth client id to ~/.radar/config.json before connecting.
            </p>
          ) : null}
          {googleError ? <p className="connection-error">{googleError}</p> : null}
        </div>

        {connected ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="connection-button"
            disabled={googleBusy}
            onClick={onDisconnectGoogle}
          >
            <Unplug size={14} strokeWidth={2.2} />
            {googleBusy ? "Disconnecting" : "Disconnect"}
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            className="connection-button connection-button--primary"
            disabled={googleBusy || !configured}
            onClick={onConnectGoogle}
          >
            <Link2 size={14} strokeWidth={2.2} />
            {googleBusy ? "Waiting" : "Connect"}
          </Button>
        )}
      </article>
    </section>
  );
}

export default App;
