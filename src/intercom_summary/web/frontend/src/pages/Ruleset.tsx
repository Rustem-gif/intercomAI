import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Button, Card, Spinner } from "@/components/ui/primitives";
import { Save } from "lucide-react";

export default function Ruleset() {
  const { user } = useAuth();
  const writer = canWrite(user?.role);
  const { data, isLoading } = useQuery({
    queryKey: ["rules"],
    queryFn: () => api.get<{ text: string; version: string }>("/api/rules"),
  });
  const [text, setText] = useState("");
  const [version, setVersion] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (data) {
      setText(data.text);
      setVersion(data.version);
    }
  }, [data]);

  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      const res = await api.put<{ version: string }>("/api/rules", { text });
      setVersion(res.version);
      setMsg("Saved. Conversations will be re-graded against this version.");
    } catch (e: any) {
      setMsg(e.message || "Save failed");
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
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Support ruleset</h1>
          <p className="text-sm text-muted-foreground">
            The policy the QA agent grades against. Version <code className="rounded bg-muted px-1">{version}</code>
          </p>
        </div>
        {writer && (
          <Button onClick={save} disabled={saving}>
            <Save className="h-4 w-4" /> {saving ? "Saving…" : "Save"}
          </Button>
        )}
      </div>

      {msg && <p className="text-sm text-emerald-500">{msg}</p>}

      <Card className="p-0">
        <textarea
          className="h-[60vh] w-full resize-none bg-transparent p-4 font-mono text-sm focus:outline-none disabled:opacity-70"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={!writer}
          spellCheck={false}
        />
      </Card>
      {!writer && <p className="text-xs text-muted-foreground">Read-only — analyst role required to edit.</p>}
    </div>
  );
}
