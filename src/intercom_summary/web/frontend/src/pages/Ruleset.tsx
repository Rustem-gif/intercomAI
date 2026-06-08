import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Button, Card, Spinner } from "@/components/ui/primitives";
import { Save } from "lucide-react";

type Tab = "qa-prompt" | "ruleset";

function Editor({
  endpoint,
  label,
  description,
  canEdit,
}: {
  endpoint: string;
  label: string;
  description: string;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: [endpoint],
    queryFn: () => api.get<{ text: string; version: string }>(`/api/${endpoint}`),
  });
  const [text, setText] = useState("");
  const [version, setVersion] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [isError, setIsError] = useState(false);

  useEffect(() => {
    if (data) {
      setText(data.text);
      setVersion(data.version);
    }
  }, [data]);

  const save = async () => {
    setSaving(true);
    setMsg("");
    setIsError(false);
    try {
      const res = await api.put<{ version: string }>(`/api/${endpoint}`, { text });
      setVersion(res.version);
      qc.invalidateQueries({ queryKey: [endpoint] });
      setMsg(`Saved (version ${res.version}). Takes effect on the next grading run.`);
    } catch (e: any) {
      setMsg(e.message || "Save failed");
      setIsError(true);
    } finally {
      setSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {description}{" "}
          <span className="font-mono text-xs">
            version <code className="rounded bg-muted px-1">{version}</code>
          </span>
        </p>
        {canEdit && (
          <Button onClick={save} disabled={saving} size="sm">
            <Save className="h-4 w-4" /> {saving ? "Saving…" : "Save"}
          </Button>
        )}
      </div>

      {msg && (
        <p className={`text-sm ${isError ? "text-destructive" : "text-emerald-500"}`}>{msg}</p>
      )}

      <Card className="p-0">
        <textarea
          className="h-[62vh] w-full resize-none bg-transparent p-4 font-mono text-sm focus:outline-none disabled:opacity-60"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={!canEdit}
          spellCheck={false}
        />
      </Card>
      {!canEdit && (
        <p className="text-xs text-muted-foreground">
          Read-only — {endpoint === "qa-prompt" ? "admin" : "analyst"} role required to edit.
        </p>
      )}
    </div>
  );
}

export default function Ruleset() {
  const { user } = useAuth();
  const writer = canWrite(user?.role);
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>("qa-prompt");

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">Scoring configuration</h1>
        <p className="text-sm text-muted-foreground">
          Edit the prompts and rules the QA engine grades against.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b">
        {(
          [
            { id: "qa-prompt" as Tab, label: "QA System Prompt", hint: "Qwen / Ollama grader" },
            { id: "ruleset" as Tab, label: "Ruleset", hint: "API / text backend" },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex flex-col px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
              tab === t.id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
            <span className="text-[10px] font-normal text-muted-foreground">{t.hint}</span>
          </button>
        ))}
      </div>

      {tab === "qa-prompt" && (
        <Editor
          endpoint="qa-prompt"
          label="QA System Prompt"
          description="System prompt sent to Qwen (Ollama) for every grading run."
          canEdit={isAdmin}
        />
      )}
      {tab === "ruleset" && (
        <Editor
          endpoint="rules"
          label="Support Ruleset"
          description="Ruleset used by the API / text grading backend."
          canEdit={writer}
        />
      )}
    </div>
  );
}
