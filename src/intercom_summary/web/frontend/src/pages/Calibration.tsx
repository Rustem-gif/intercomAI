import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Download, Play, Square } from "lucide-react";
import {
  api,
  CalibrationResults,
  CalibrationRow,
  CalibrationSample,
} from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Badge, Button, Card, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { cn, fmtDate, scoreColor } from "@/lib/utils";

// Temporary page: the QA department's frozen calibration sample, graded by the Claude grader
// into its own run (never into live grades), next to the QA managers' own verdicts.

const ACTIVE = ["queued", "running", "cancelling"];

const STATUS_LABEL: Record<string, string> = {
  graded: "graded",
  failed: "failed",
  not_found: "not in Intercom",
  ticket: "ticket — not graded",
};

type ReviewFilter = "all" | "reviewed" | "unreviewed" | "gap";
type SortKey = "seq" | "delta" | "ai";

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value ?? "—"}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}

function Flags({ r }: { r: CalibrationRow }) {
  return (
    <div className="flex flex-wrap gap-1">
      {r.critical_fail && (
        <Badge className="border-destructive/40 bg-destructive/10 text-destructive">critical</Badge>
      )}
      {r.catastrophic && (
        <Badge className="border-destructive/40 bg-destructive/10 text-destructive">catastrophic</Badge>
      )}
      {r.manual_review_needed && (
        <Badge className="border-amber-500/40 bg-amber-500/10 text-amber-600">manual review</Badge>
      )}
    </div>
  );
}

export default function Calibration() {
  const { user } = useAuth();
  const writer = canWrite(user?.role);
  const qc = useQueryClient();
  const [sampleId, setSampleId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [pilotOnly, setPilotOnly] = useState(false);
  const [category, setCategory] = useState("");
  const [review, setReview] = useState<ReviewFilter>("all");
  const [sort, setSort] = useState<SortKey>("seq");
  const [batch, setBatch] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  const samples = useQuery({
    queryKey: ["calibration-samples"],
    queryFn: () => api.get<{ items: CalibrationSample[] }>("/api/calibration/samples"),
  });
  const sid = sampleId ?? samples.data?.items[0]?.id ?? null;

  const results = useQuery({
    queryKey: ["calibration-results", sid, runId],
    queryFn: () =>
      api.get<CalibrationResults>(`/api/calibration/samples/${sid}/results?run=${runId ?? "latest"}`),
    enabled: !!sid,
    refetchInterval: (q) =>
      ACTIVE.includes(q.state.data?.active_job?.status ?? "") ? 3000 : false,
  });
  const data = results.data;
  const job = data?.active_job ?? null;
  const running = !!job && ACTIVE.includes(job.status);

  // Pick up the finished run as soon as the job ends.
  const [wasRunning, setWasRunning] = useState(false);
  useEffect(() => {
    if (running) setWasRunning(true);
    else if (wasRunning) {
      setWasRunning(false);
      qc.invalidateQueries({ queryKey: ["calibration-results", sid] });
    }
  }, [running]);

  const categories = useMemo(
    () => Array.from(new Set((data?.rows ?? []).map((r) => r.category).filter(Boolean))) as string[],
    [data],
  );

  const rows = useMemo(() => {
    let out = (data?.rows ?? []).filter(
      (r) =>
        (!pilotOnly || r.pilot) &&
        (!category || r.category === category) &&
        (review === "all" ||
          (review === "reviewed" && r.human_score != null) ||
          (review === "unreviewed" && r.status === "graded" && r.human_score == null) ||
          (review === "gap" && r.delta != null && Math.abs(r.delta) >= 10)),
    );
    if (sort === "delta")
      out = [...out].sort((a, b) => Math.abs(b.delta ?? -1) - Math.abs(a.delta ?? -1));
    if (sort === "ai") out = [...out].sort((a, b) => (a.ai_score ?? 999) - (b.ai_score ?? 999));
    return out;
  }, [data, pilotOnly, category, review, sort]);

  const start = async () => {
    if (!sid) return;
    // A new run starts with no QA verdicts; the ones already entered stay on their own run.
    const reviewed = data?.summary?.reviewed ?? 0;
    if (data?.run && !window.confirm(
      `Start a new run of ${data.sample.size} chats (~$${(data.sample.size * 0.035).toFixed(0)})?` +
      (reviewed ? `\n\n${reviewed} QA verdict(s) on the current run will stay on that run — ` +
        "pick it in the run selector to see them." : ""))) return;
    setStarting(true);
    setError("");
    try {
      await api.post(`/api/calibration/samples/${sid}/run`, { ruleset_id: "kb-v41", batch });
      setRunId(null);
      qc.invalidateQueries({ queryKey: ["calibration-results", sid] });
    } catch (e: any) {
      setError(e.message || "Could not start the run");
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    await api.post(`/api/jobs/${job.id}/cancel`).catch(() => undefined);
    qc.invalidateQueries({ queryKey: ["calibration-results", sid] });
  };

  const s = data?.summary;
  const run = data?.run;
  const progress = job?.result?.total
    ? `${(job.result.graded ?? 0) + (job.result.skipped ?? 0)} / ${job.result.total}`
    : "preparing…";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <FlaskConical className="h-5 w-5 text-primary" />
            <h1 className="text-2xl font-bold">Calibration</h1>
            <Badge className="border-amber-500/40 bg-amber-500/10 text-amber-600">temporary</Badge>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            {data?.sample.name ?? "Calibration sample"} graded by Claude on the v4.1 model,
            exactly as live chats are. These grades are kept apart from the dashboards. Open a
            chat to record your own verdict; the gap to the AI is shown here.
          </p>
          {run && (
            <p className="mt-1 text-xs text-muted-foreground">
              Run {run.id} · {run.model} ({run.effort}) · ruleset {run.ruleset_id} ·{" "}
              {fmtDate(run.started_at)}
              {run.cost_usd != null && ` · $${run.cost_usd.toFixed(2)}`}
              {!run.finished_at && !running && " · unfinished"}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {(samples.data?.items.length ?? 0) > 1 && (
            <select
              className="h-8 rounded-md border bg-background px-2 text-xs"
              value={sid ?? ""}
              onChange={(e) => { setSampleId(e.target.value); setRunId(null); }}
            >
              {samples.data!.items.map((x) => (
                <option key={x.id} value={x.id}>{x.name}</option>
              ))}
            </select>
          )}
          {(data?.runs.length ?? 0) > 1 && (
            <select
              className="h-8 rounded-md border bg-background px-2 text-xs"
              value={runId ?? ""}
              onChange={(e) => setRunId(e.target.value || null)}
              title="Each run keeps its own AI grades and QA verdicts"
            >
              <option value="">Latest run</option>
              {data!.runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {fmtDate(r.started_at)} · {r.id}
                </option>
              ))}
            </select>
          )}
          {run && (
            <Button variant="outline" size="sm" onClick={() => {
              window.location.href = `/api/calibration/samples/${sid}/export.xlsx?run=${run.id}`;
            }}>
              <Download className="h-3.5 w-3.5" /> Export XLSX
            </Button>
          )}
          {writer && !running && (
            <>
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground"
                     title="Message Batches API: half price, results in minutes to hours">
                <input type="checkbox" checked={batch} onChange={(e) => setBatch(e.target.checked)} />
                batch
              </label>
              <Button size="sm" disabled={starting || !sid} onClick={start}>
                {starting ? <Spinner className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                {run ? "Run again" : "Run evaluation"}
              </Button>
            </>
          )}
          {running && (
            <>
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <Spinner className="h-3.5 w-3.5 text-primary" /> Grading {progress}
              </span>
              {writer && (
                <Button size="sm" variant="outline" onClick={cancel}>
                  <Square className="h-3.5 w-3.5" /> Cancel
                </Button>
              )}
            </>
          )}
        </div>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {job?.status === "error" && <p className="text-sm text-destructive">{job.error}</p>}

      {results.isLoading || samples.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Spinner className="h-6 w-6 text-primary" />
        </div>
      ) : !sid ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">
          No calibration sample has been imported.
        </Card>
      ) : !run ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">
          {running ? "The first run is in progress…" : "Not evaluated yet."}
        </Card>
      ) : (
        <>
          {s && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <Stat label="Graded" value={`${s.graded} / ${s.members}`}
                    hint={Object.entries(s.status).filter(([k]) => k !== "graded")
                      .map(([k, n]) => `${n} ${STATUS_LABEL[k] ?? k.replace("_", " ")}`).join(" · ")} />
              <Stat label="Mean AI score" value={s.mean_ai}
                    hint={`${s.manual_review} need manual review`} />
              <Stat label="Reviewed by QA" value={`${s.reviewed} / ${s.graded}`} />
              <Stat label="Mean QA score" value={s.mean_human}
                    hint={s.mean_ai_reviewed != null ? `AI on the same chats: ${s.mean_ai_reviewed}` : undefined} />
              <Stat label="Mean |Δ|" value={s.mean_abs_delta}
                    hint={s.mean_delta != null ? `signed ${s.mean_delta > 0 ? "+" : ""}${s.mean_delta} (QA − AI)` : undefined} />
              <Stat label="Criteria agreement" value={s.criteria_agreement != null ? `${s.criteria_agreement}%` : null}
                    hint="reviewed chats, per criterion" />
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={pilotOnly} onChange={(e) => setPilotOnly(e.target.checked)} />
              Pilot-20 only
            </label>
            <select className="h-8 rounded-md border bg-background px-2" value={category}
                    onChange={(e) => setCategory(e.target.value)}>
              <option value="">All categories</option>
              {categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className="h-8 rounded-md border bg-background px-2" value={review}
                    onChange={(e) => setReview(e.target.value as ReviewFilter)}>
              <option value="all">All chats</option>
              <option value="unreviewed">Awaiting QA review</option>
              <option value="reviewed">Reviewed by QA</option>
              <option value="gap">|Δ| ≥ 10</option>
            </select>
            <select className="h-8 rounded-md border bg-background px-2" value={sort}
                    onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="seq">Document order</option>
              <option value="delta">Largest gap first</option>
              <option value="ai">Lowest AI score first</option>
            </select>
            <span className="text-muted-foreground">{rows.length} shown</span>
          </div>

          <Card className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2.5 font-medium">№</th>
                  <th className="px-3 py-2.5 font-medium">Chat</th>
                  <th className="px-3 py-2.5 font-medium">Agent</th>
                  <th className="px-3 py-2.5 font-medium">Category</th>
                  <th className="px-3 py-2.5 font-medium">AI</th>
                  <th className="px-3 py-2.5 font-medium">Flags</th>
                  <th className="px-3 py-2.5 font-medium">QA</th>
                  <th className="px-3 py-2.5 font-medium">Δ</th>
                  <th className="px-3 py-2.5 font-medium" title="Criteria where QA's verdict differs from the AI's">≠ criteria</th>
                  <th className="px-3 py-2.5 font-medium" title="The chat's grade on the dashboards (old model)">Live</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const openable = r.status === "graded" || r.status === "ticket";
                  return (
                    <tr
                      key={r.conversation_id}
                      onClick={() => openable && setOpenId(r.conversation_id)}
                      className={cn("border-b last:border-0", openable && "cursor-pointer hover:bg-muted/50")}
                    >
                      <td className="px-3 py-2.5 text-muted-foreground">
                        {r.seq}
                        {r.pilot && <span className="ml-1 text-[10px] font-semibold text-primary">P</span>}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="font-mono text-xs">{r.conversation_id}</div>
                        <div className="text-xs text-muted-foreground">
                          {r.chat_date}
                          {r.source === "intercom" && " · fetched"}
                        </div>
                      </td>
                      <td className="px-3 py-2.5">{r.agent_name}</td>
                      <td className="px-3 py-2.5 text-xs text-muted-foreground">{r.category}</td>
                      <td className="px-3 py-2.5">
                        {r.status === "graded" ? (
                          <div>
                            <span className={cn("font-bold", scoreColor(r.ai_score))}>{r.ai_score}</span>
                            {r.band && <span className="ml-1 text-xs text-muted-foreground">{r.band}</span>}
                          </div>
                        ) : (
                          <span className="text-xs italic text-muted-foreground" title={r.error ?? undefined}>
                            {r.status ? STATUS_LABEL[r.status] : "not run"}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5"><Flags r={r} /></td>
                      <td className={cn("px-3 py-2.5 font-bold", scoreColor(r.human_score))}
                          title={r.human_note ? `${r.reviewed_by}: ${r.human_note}` : undefined}>
                        {r.human_score ?? "—"}
                      </td>
                      <td className={cn("px-3 py-2.5 font-semibold tabular-nums",
                        r.delta == null ? "text-muted-foreground"
                          : Math.abs(r.delta) >= 10 ? "text-destructive" : "text-emerald-600")}>
                        {r.delta == null ? "—" : r.delta > 0 ? `+${r.delta}` : r.delta}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">{r.disagreed ?? "—"}</td>
                      <td className="px-3 py-2.5 text-xs text-muted-foreground">{r.live_score ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        </>
      )}

      {openId && run && (
        <ConversationDrawer
          id={openId}
          onClose={() => setOpenId(null)}
          detailUrl={`/api/calibration/runs/${run.id}/conversations/${openId}`}
          overrideUrl={`/api/calibration/runs/${run.id}/conversations/${openId}/review`}
          onOverridden={() => qc.invalidateQueries({ queryKey: ["calibration-results", sid] })}
        />
      )}
    </div>
  );
}
