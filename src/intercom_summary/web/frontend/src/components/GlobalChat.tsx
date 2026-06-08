import { useEffect, useMemo, useRef, useState } from "react";
import { X, Send, Database, BarChart2, MessageSquare, Users } from "lucide-react";
import { Button, Input, Spinner } from "./ui/primitives";
import QwenIcon from "./QwenIcon";

const PANEL_W = 420;
const PANEL_H = 600;
const BTN_SIZE = 56;
const GAP = 10;

function anchorPos(btn: { x: number; y: number }) {
  // Right-align panel with button's right edge, open above by default.
  let left = btn.x + BTN_SIZE - PANEL_W;
  let top  = btn.y - PANEL_H - GAP;

  // If not enough room above, open below.
  if (top < GAP) top = btn.y + BTN_SIZE + GAP;

  // Clamp to viewport.
  left = Math.max(GAP, Math.min(window.innerWidth  - PANEL_W - GAP, left));
  top  = Math.max(GAP, Math.min(window.innerHeight - PANEL_H - GAP, top));

  return { left, top };
}

// ── Types ──────────────────────────────────────────────────────────────────────
interface TextMessage { kind: "text"; role: "user" | "assistant"; content: string }
interface ToolEvent   { kind: "tool"; name: string; args: Record<string, unknown>; preview?: string }
type ChatItem = TextMessage | ToolEvent;

const TOOL_LABELS: Record<string, { label: string; Icon: React.ElementType }> = {
  search_conversations: { label: "Searching conversations",  Icon: Database },
  get_conversation:     { label: "Reading conversation",     Icon: MessageSquare },
  get_agent_stats:      { label: "Fetching agent stats",     Icon: BarChart2 },
  list_agents:          { label: "Listing agents",           Icon: Users },
};

// ── ToolCard ──────────────────────────────────────────────────────────────────
function ToolCard({ event }: { event: ToolEvent }) {
  const [open, setOpen] = useState(false);
  const meta = TOOL_LABELS[event.name] ?? { label: event.name, Icon: Database };
  return (
    <div className="my-1 rounded-md border border-primary/20 bg-primary/5 px-3 py-1.5 text-xs">
      <button
        className="flex w-full items-center gap-2 text-left text-primary"
        onClick={() => setOpen((v) => !v)}
      >
        <meta.Icon className="h-3.5 w-3.5 shrink-0" />
        <span className="font-medium">{meta.label}</span>
        {Object.keys(event.args).length > 0 && (
          <span className="ml-1 text-muted-foreground">
            {Object.entries(event.args)
              .filter(([, v]) => v !== undefined && v !== null && v !== "")
              .map(([k, v]) => `${k}: ${String(v)}`)
              .join(" · ")}
          </span>
        )}
        <span className="ml-auto text-muted-foreground">{open ? "▲" : "▼"}</span>
      </button>
      {open && event.preview && (
        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-[10px] text-muted-foreground">
          {event.preview}
        </pre>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function GlobalChat({
  onClose,
  buttonPos,
}: {
  onClose: () => void;
  buttonPos: { x: number; y: number };
}) {
  const panelPos = useMemo(() => anchorPos(buttonPos), [buttonPos]);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setError("");

    // Build history from text messages only (tools are UI-only).
    const history = items
      .filter((it): it is TextMessage => it.kind === "text")
      .map((m) => ({ role: m.role, content: m.content }));

    setItems((prev) => [...prev, { kind: "text", role: "user", content: text }]);
    setStreaming(true);

    // Placeholder for the assistant reply that we'll fill in via streaming.
    setItems((prev) => [...prev, { kind: "text", role: "assistant", content: "" }]);

    try {
      const res = await fetch("/api/ai/agent", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: "", message: text, history }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop()!;

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6);
          if (raw === "[DONE]") break;
          try {
            const ev = JSON.parse(raw);

            if (ev.token) {
              setItems((prev) => {
                const copy = [...prev];
                const last = copy[copy.length - 1] as TextMessage;
                copy[copy.length - 1] = { ...last, content: last.content + ev.token };
                return copy;
              });
            } else if (ev.tool_call) {
              // Insert tool event BEFORE the trailing assistant placeholder.
              setItems((prev) => {
                const copy = [...prev];
                const placeholder = copy.pop()!;
                return [
                  ...copy,
                  { kind: "tool", name: ev.tool_call, args: ev.args ?? {} } as ToolEvent,
                  placeholder,
                ];
              });
            } else if (ev.tool_result) {
              // Attach preview to the most recent matching tool event.
              setItems((prev) =>
                prev.map((it) =>
                  it.kind === "tool" && it.name === ev.tool_result && !it.preview
                    ? { ...it, preview: ev.preview }
                    : it,
                ),
              );
            } else if (ev.error) {
              setError(ev.error);
            }
          } catch {
            // malformed line
          }
        }
      }
    } catch (e: any) {
      setError(e.message || "Request failed");
      // Remove empty assistant placeholder on error.
      setItems((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1] as TextMessage;
        if (last?.kind === "text" && last.role === "assistant" && !last.content) copy.pop();
        return copy;
      });
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div
      style={{ left: panelPos.left, top: panelPos.top, width: PANEL_W, height: PANEL_H }}
      className="fixed z-50 flex flex-col overflow-hidden rounded-2xl border bg-background shadow-2xl"
    >
      {/* Header */}
      <div className="flex shrink-0 items-center gap-3 border-b bg-card px-4 py-3">
        <QwenIcon className="h-8 w-8 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Qwen QA Agent</p>
          <p className="truncate text-xs text-muted-foreground">
            Searches conversations · Reads transcripts · Analyses agents
          </p>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} className="shrink-0">
          <X className="h-4 w-4" />
        </Button>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-2 overflow-auto p-4">
        {items.length === 0 && (
          <div className="flex flex-col items-center gap-3 pt-8 text-center">
            <QwenIcon className="h-12 w-12 opacity-60" />
            <p className="text-sm text-muted-foreground">
              Ask me anything about your support conversations.
            </p>
            <div className="space-y-1 text-xs text-muted-foreground">
              {[
                "How is agent Oswald performing?",
                "Show me the worst-scored conversations this week",
                "Find conversations about withdrawal issues",
              ].map((s) => (
                <button
                  key={s}
                  onClick={() => { setInput(s); }}
                  className="block w-full rounded-md border px-3 py-1.5 text-left hover:bg-muted"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {items.map((item, i) => {
          if (item.kind === "tool") return <ToolCard key={i} event={item} />;
          const isUser = item.role === "user";
          return (
            <div key={i} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
              {!isUser && (
                <QwenIcon className="mr-2 mt-1 h-5 w-5 shrink-0 self-start" />
              )}
              <div
                className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm ${
                  isUser
                    ? "rounded-br-sm bg-primary text-primary-foreground"
                    : "rounded-bl-sm bg-muted"
                }`}
              >
                {item.content || (streaming && i === items.length - 1 ? (
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <Spinner className="h-3 w-3" /> Thinking…
                  </span>
                ) : null)}
              </div>
            </div>
          );
        })}

        {error && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex shrink-0 gap-2 border-t p-3">
        <Input
          className="flex-1"
          placeholder="Ask about conversations, agents, scores…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          disabled={streaming}
        />
        <Button size="icon" onClick={send} disabled={!input.trim() || streaming}>
          {streaming ? <Spinner className="h-4 w-4" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
}
