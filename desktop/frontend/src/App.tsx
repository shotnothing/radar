import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  Bell,
  Bot,
  Chrome,
  Cpu,
  FileText,
  Link2,
  Layers2,
  Loader2,
  Mail,
  MessageCircle,
  Play,
  Settings2,
  Sparkles,
  Square,
  Unplug,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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

type SettingsSection = "collector" | "processor" | "actor" | "connection";

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
  chrome_bridge_url: string;
  work_dir: string;
};

const settingsTabs: Array<{
  id: SettingsSection;
  label: string;
  icon: LucideIcon;
}> = [
  { id: "collector", label: "collector", icon: Settings2 },
  { id: "processor", label: "processor", icon: Cpu },
  { id: "actor", label: "actor", icon: Bot },
  { id: "connection", label: "connection", icon: Link2 },
];

const settingsCopy: Record<
  SettingsSection,
  { title: string; description: string; empty: string }
> = {
  collector: {
    title: "collector",
    description: "Local sources that collect activity signals.",
    empty: "No collectors configured.",
  },
  processor: {
    title: "processor",
    description: "Pipelines that turn collected signals into intent.",
    empty: "No processors configured.",
  },
  actor: {
    title: "actor",
    description: "Actions that can be triggered after intent is detected.",
    empty: "No actors configured yet.",
  },
  connection: {
    title: "connection",
    description: "External accounts Radar can use with your permission.",
    empty: "No connections configured yet.",
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
      name: "YouTube Search NAB",
      description: "Fills the active YouTube search box with NAB.",
      status: "builtin.youtube_search_nab",
      icon: Workflow,
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
  ],
  connection: [],
};

function getInitialModuleState() {
  return Object.fromEntries(
    Object.values(moduleCatalog)
      .flat()
      .map((item) => [item.id, item.defaultEnabled])
  );
}

function AssistantWindow() {
  const [currentSuggestion, setCurrentSuggestion] = useState<Suggestion | null>(
    null
  );
  const [queue, setQueue] = useState<Suggestion[]>([]);
  const [feedback, setFeedback] = useState("");
  const [choice, setChoice] = useState("later");
  const currentSuggestionRef = useRef<Suggestion | null>(null);
  const queueRef = useRef<Suggestion[]>([]);

  useEffect(() => {
    const unlisten = listen<Suggestion>(
      "radar://mock-suggestion",
      async (event) => {
        const suggestion = event.payload;
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
    );

    return () => {
      void unlisten.then((dispose) => dispose());
    };
  }, []);

  function resetControls() {
    setFeedback("");
    setChoice("later");
  }

  function activateSuggestion(suggestion: Suggestion) {
    resetControls();
    currentSuggestionRef.current = suggestion;
    setCurrentSuggestion(suggestion);
  }

  async function completeCurrent(runActor = false) {
    const suggestion = currentSuggestionRef.current;
    if (suggestion?.actor_id && suggestion.trigger_id) {
      try {
        await invoke("complete_actor_suggestion", {
          actorId: suggestion.actor_id,
          triggerId: suggestion.trigger_id,
          run: runActor,
        });
      } catch (error) {
        console.error("Failed to complete actor suggestion:", error);
      }
    }

    const [nextSuggestion, ...remainingSuggestions] = queueRef.current;
    queueRef.current = remainingSuggestions;
    setQueue(remainingSuggestions);

    if (nextSuggestion) {
      activateSuggestion(nextSuggestion);
      return;
    }

    currentSuggestionRef.current = null;
    setCurrentSuggestion(null);
    await getCurrentWindow().hide();
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
          <ReactMarkdown>{`**${currentSuggestion.title}**\n\n${currentSuggestion.body}`}</ReactMarkdown>
        </section>

        <section className="controls" aria-label="Suggestion actions">
          <Select value={choice} onValueChange={setChoice}>
            <SelectTrigger
              aria-label="Reminder timing"
              className="h-7 w-[104px] rounded-[9px] bg-white text-xs shadow-none"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent side="top" className="rounded-xl">
              <SelectItem value="later">Remind later</SelectItem>
              <SelectItem value="today">Keep today</SelectItem>
              <SelectItem value="never">Never remind</SelectItem>
            </SelectContent>
          </Select>
          <Input
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="Add preference"
            aria-label="Feedback"
            className="h-7 rounded-[9px] bg-white text-xs shadow-none md:text-xs"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 rounded-[9px] px-2.5 text-xs text-slate-600 hover:bg-white/65 hover:text-slate-900"
            onClick={() => void completeCurrent(false)}
          >
            Dismiss
          </Button>
          <Button
            type="button"
            size="sm"
            className="h-7 rounded-[9px] bg-slate-950 px-2.5 text-xs text-white shadow-sm hover:bg-slate-800"
            onClick={() => void completeCurrent(true)}
          >
            {currentSuggestion.primary_action}
          </Button>
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
