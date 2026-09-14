import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BlastRadius, type QaRuleset } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Button, Card, Spinner } from "@/components/ui/primitives";
import { Save, AlertTriangle } from "lucide-react";

function Editor({
  endpoint,
  description,
  canEdit,
  roleHint,
  ruleset,
}: {
  endpoint: string;
  description: string;
  canEdit: boolean;
  roleHint: string;
  ruleset?: QaRuleset;
}) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: [endpoint],
    queryFn: () => api.get<{ text: string; version: string; blast_radius?: BlastRadius }>(
      `/api/${endpoint}`),
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
      qc.invalidateQueries({ queryKey: ["rulesets"] });
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

  // A gated ruleset's deduction column no longer tells the whole story — a Major caps the
  // score rather than subtracting from it — so the table grows two columns for it.
  const gated = ruleset?.scoring?.model === "gated";
  const blast = data?.blast_radius;

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

      {/* What saving an edit actually costs. The version is a hash of this text, so changing a
          single word marks every grade this ruleset produced stale and the next run re-grades
          them with different scores. In September that landed on work QA had already reviewed
          and agents had already disputed, and nothing here said it was about to happen. */}
      {blast && blast.graded_at_current_version > 0 && (
        <Card className="border-amber-500/50 bg-amber-500/5 p-3">
          <div className="mb-1 flex items-center gap-2 text-sm font-medium text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-4 w-4" />
            Editing this prompt re-grades {blast.would_regrade.toLocaleString()} conversations
          </div>
          <p className="text-xs text-muted-foreground">
            Any change to the text below — even one word — creates a new version, which marks
            all {blast.graded_at_current_version.toLocaleString()} grades from this ruleset
            outdated. The next grading run re-scores them, and the new scores will not match
            the old ones.
            {blast.protected_by_human_review > 0 && (
              <>
                {" "}
                {blast.protected_by_human_review.toLocaleString()} of them have already been
                re-graded by an analyst and are left alone.
              </>
            )}
          </p>
        </Card>
      )}

      {/* The deduction points live in two places: the table inside the prompt text (what the
          model applies while grading) and the criteria catalogue (what an analyst's manual
          re-score applies). If they disagree, the same criterion costs different points
          depending on who scored it — so say so loudly. */}
      {ruleset && ruleset.warnings.length > 0 && (
        <Card className="border-amber-500/50 bg-amber-500/5 p-3">
          <div className="mb-1 flex items-center gap-2 text-sm font-medium text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-4 w-4" />
            Prompt and criteria catalogue disagree
          </div>
          <ul className="list-inside list-disc space-y-0.5 text-xs text-muted-foreground">
            {ruleset.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted-foreground">
            Fix by aligning the “Ded” column in the prompt below with{" "}
            <code className="rounded bg-muted px-1">config/rulesets.yaml</code>.
          </p>
        </Card>
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
          Read-only — {roleHint} role required to edit.
        </p>
      )}

      {ruleset && (
        <Card className="p-4">
          <h3 className="mb-2 text-sm font-medium">
            Criteria ({ruleset.criteria.length}) — used for manual re-scoring
          </h3>
          {/* The thresholds the QA manual deliberately leaves to the QC manager. They are shown
              here because a number nobody can see is a number nobody can calibrate. */}
          {gated && (
            <div className="mb-3 rounded-md border bg-muted/40 p-3 text-xs">
              <div className="mb-1 font-medium">Gated scoring (QA Manual v4.1)</div>
              <ul className="space-y-0.5 text-muted-foreground">
                <li>PASS at <b>{ruleset.scoring.pass_threshold}</b> or above.</li>
                <li>
                  Any Gate 2 Major caps the chat at <b>{ruleset.scoring.major_cap}</b> — however
                  many of them there are.
                </li>
                <li>
                  Without a Major the score never falls below <b>{ruleset.scoring.no_major_floor}</b>,
                  so a chat that served the player can never rank below one that did not.
                </li>
                <li>
                  Outcome failure plus every process check failing at once scores{" "}
                  <b>{ruleset.scoring.catastrophic_score}</b>, flagged separately from a
                  compliance breach.
                </li>
                {ruleset.scoring.group_caps &&
                  Object.entries(ruleset.scoring.group_caps).map(([g, cap]) => (
                    <li key={g}>
                      The <b>{g}</b> criteria are capped together at <b>−{cap}</b>.
                    </li>
                  ))}
              </ul>
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-muted-foreground">
                <tr>
                  <th className="py-1 pr-4 font-medium">ID</th>
                  <th className="py-1 pr-4 font-medium">Title</th>
                  {gated && <th className="py-1 pr-4 font-medium">Gate</th>}
                  {gated && <th className="py-1 pr-4 font-medium">Severity</th>}
                  <th className="py-1 pr-4 font-medium">Score impact</th>
                </tr>
              </thead>
              <tbody>
                {ruleset.criteria.map((c) => (
                  <tr key={c.id} className="border-t">
                    <td className="py-1 pr-4 font-mono">{c.id}</td>
                    <td className="py-1 pr-4">{c.title}</td>
                    {gated && <td className="py-1 pr-4">{c.gate ?? "—"}</td>}
                    {gated && <td className="py-1 pr-4">{c.severity ?? "—"}</td>}
                    <td className="py-1 pr-4">
                      {c.critical ? (
                        <span className="font-medium text-destructive">critical → 0</span>
                      ) : c.severity === "major" ? (
                        <span className="font-medium text-orange-600 dark:text-orange-400">
                          caps at {ruleset.scoring.major_cap}
                        </span>
                      ) : c.group ? (
                        `−${c.deduction} (with ${c.group}, capped at −${ruleset.scoring.group_caps?.[c.group] ?? "?"})`
                      ) : (
                        `−${c.deduction}`
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Criteria are defined in{" "}
            <code className="rounded bg-muted px-1">config/rulesets.yaml</code>.
          </p>
        </Card>
      )}
    </div>
  );
}

export default function Ruleset() {
  const { user } = useAuth();
  const writer = canWrite(user?.role);
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<string>("default");

  const { data } = useQuery({
    queryKey: ["rulesets"],
    queryFn: () => api.get<{ items: QaRuleset[] }>("/api/rulesets"),
  });
  const rulesets = data?.items ?? [];
  const active = rulesets.find((r) => r.id === tab);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">Scoring configuration</h1>
        <p className="text-sm text-muted-foreground">
          Each agent group is graded against its own ruleset. A conversation is scored with the
          ruleset of the agent it is assigned to — VIP agents’ chats and emails use the VIP
          ruleset. Because the criteria differ, scores from different rulesets are not
          comparable.
        </p>
      </div>

      {/* One tab per QA ruleset, plus the legacy API-backend ruleset. */}
      <div className="flex gap-1 border-b">
        {rulesets.map((r) => (
          <button
            key={r.id}
            onClick={() => setTab(r.id)}
            className={`flex flex-col px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
              tab === r.id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            <span className="flex items-center gap-1.5">
              {r.name}
              {r.warnings.length > 0 && <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />}
            </span>
            <span className="text-[10px] font-normal text-muted-foreground">
              {r.id === "vip" ? "VIP agents" : "everyone else"} · {r.criteria.length} criteria
            </span>
          </button>
        ))}
        <button
          onClick={() => setTab("rules")}
          className={`flex flex-col px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
            tab === "rules"
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          Support Ruleset
          <span className="text-[10px] font-normal text-muted-foreground">API / text backend</span>
        </button>
      </div>

      {tab === "rules" ? (
        <Editor
          endpoint="rules"
          description="Ruleset used by the legacy API / text grading backend."
          canEdit={writer}
          roleHint="analyst"
        />
      ) : (
        active && (
          <Editor
            key={active.id}
            endpoint={`rulesets/${active.id}/prompt`}
            description={`System prompt sent to Qwen (Ollama) when grading ${
              active.id === "vip" ? "VIP agents’" : "standard agents’"
            } conversations.`}
            canEdit={isAdmin}
            roleHint="admin"
            ruleset={active}
          />
        )
      )}
    </div>
  );
}
