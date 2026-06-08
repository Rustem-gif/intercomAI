import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { Button, Input, Spinner } from "./ui/primitives";

interface Message {
  role: "user" | "assistant";
  content: string;
}

export default function AiChatPanel({ conversationId }: { conversationId: string }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput("");
    setError("");

    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", content: "" },
    ]);
    setStreaming(true);

    try {
      const res = await fetch("/api/ai/chat", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId, message: text, history }),
      });

      if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`);
      }

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
            const parsed = JSON.parse(raw);
            if (parsed.error) {
              setError(parsed.error);
              break;
            }
            if (parsed.token) {
              setMessages((prev) => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                copy[copy.length - 1] = { ...last, content: last.content + parsed.token };
                return copy;
              });
            }
          } catch {
            // malformed line, skip
          }
        }
      }
    } catch (e: any) {
      setError(e.message || "Request failed");
      setMessages((prev) => prev.slice(0, -1)); // remove empty assistant placeholder
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      {/* Message list */}
      <div className="flex-1 space-y-3 overflow-auto p-4">
        {messages.length === 0 && (
          <p className="pt-8 text-center text-sm text-muted-foreground">
            Ask Qwen anything about this conversation.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"
              }`}
            >
              {m.content ? (
                m.content
              ) : streaming && i === messages.length - 1 ? (
                <Spinner className="h-3 w-3" />
              ) : null}
            </div>
          </div>
        ))}
        {error && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex gap-2 border-t p-3">
        <Input
          className="flex-1"
          placeholder="Ask about this conversation…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          disabled={streaming}
        />
        <Button size="icon" onClick={send} disabled={!input.trim() || streaming}>
          {streaming ? <Spinner className="h-4 w-4" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
}
