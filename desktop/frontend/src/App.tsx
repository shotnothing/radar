import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  Bell,
  Bot,
  Chrome,
  Cpu,
  FileText,
  Layers2,
  MessageCircle,
  Settings2,
  Sparkles,
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
  id: number;
  title: string;
  body: string;
  primary_action: string;
};

type SettingsSection = "collector" | "processor" | "actor";

type ModuleConfig = {
  id: string;
  name: string;
  description: string;
  status: string;
  icon: LucideIcon;
  defaultEnabled: boolean;
};

const settingsTabs: Array<{
  id: SettingsSection;
  label: string;
  icon: LucideIcon;
}> = [
  { id: "collector", label: "collector", icon: Settings2 },
  { id: "processor", label: "processor", icon: Cpu },
  { id: "actor", label: "actor", icon: Bot },
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
  actor: [],
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

  async function completeCurrent() {
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
        <header className="assistant-header">
          <div className="assistant-brand">
            <span className="assistant-mark" aria-hidden="true">
              <Sparkles size={14} strokeWidth={2.2} />
            </span>
            <span>Radar</span>
          </div>
          {queue.length > 0 ? (
            <span
              className="queue-badge"
              aria-label={`${queue.length} suggestions waiting`}
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
              <SelectItem value="later">稍后提醒</SelectItem>
              <SelectItem value="today">今天保持</SelectItem>
              <SelectItem value="never">不再提示</SelectItem>
            </SelectContent>
          </Select>
          <Input
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="补充偏好"
            aria-label="Feedback"
            className="h-7 rounded-[9px] bg-white text-xs shadow-none md:text-xs"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 rounded-[9px] px-2.5 text-xs text-slate-600 hover:bg-white/65 hover:text-slate-900"
            onClick={completeCurrent}
          >
            忽略
          </Button>
          <Button
            type="button"
            size="sm"
            className="h-7 rounded-[9px] bg-slate-950 px-2.5 text-xs text-white shadow-sm hover:bg-slate-800"
            onClick={completeCurrent}
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

  const activeCopy = settingsCopy[activeSection];
  const activeItems = moduleCatalog[activeSection];

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
        </header>

        {activeItems.length > 0 ? (
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

export default App;
