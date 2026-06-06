import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { Layers2, Sparkles } from "lucide-react";
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

  return (
    <Card className="settings-window" role="main" aria-label="Radar settings" />
  );
}

export default App;
