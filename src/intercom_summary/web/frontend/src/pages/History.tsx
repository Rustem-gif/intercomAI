import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { History, RefreshCw } from "lucide-react";
import { api, HistoryStats, JobListItem } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Badge, Button, Card, Spinner } from "@/components/ui/primitives";
import { fmtDate } from "@/lib/utils";

// The labelled chat history the grader reads for context: what each player was told before,
// promises made, and how QA ruled on similar cases (docs/history-context.md). This page shows
// how much of the history is indexed and lets an analyst refresh it by hand; the web app also
// refreshes it on its own every HISTORY_REFRESH_DAYS.

const ACTIVE = ["queued", "running", "cancelling"];

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value ?? "—"}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}

function Counts({ title, rows }: { title: string; rows: { key: string; n: number }[] }) {
  return (
    <Card className="p-4">
      <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">—</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {rows.map((r) => (
            <li key={r.key || "(none)"} className="flex justify-between gap-3">
              <span className="truncate">{r.key || "(none)"}</span>
              <span className="tabular-nums text-muted-foreground">{r.n}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function HistoryIndex() {
  const { user } = useAuth();
  const writer = canWrite(user?.role);
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  const stats = useQuery({
    queryKey: ["history-stats"],
    queryFn: () => api.get<HistoryStats>("/api/history/stats"),
  });
  const jobs = useQuery({
    queryKey: ["history-jobs"],
    queryFn: () => api.get<{ jobs: JobListItem[] }>("/api/jobs?kind=history_refresh&limit=5"),
    refetchInterval: (q) =>
      (q.state.data?.jobs ?? []).some((j) => ACTIVE.includes(j.status)) ? 3000 : false,
  });
  const job = jobs.data?.jobs[0] ?? null;
  const running = !!job && ACTIVE.includes(job.status);

  const [wasRunning, setWasRunning] = useState(false);
  useEffect(() => {
    if (running) setWasRunning(true);
    else if (wasRunning) {
      setWasRunning(false);
      qc.invalidateQueries({ queryKey: ["history-stats"] });
    }
  }, [running]);

  const refresh = async () => {
    setStarting(true);
    setError("");
    try {
      await api.post("/api/history/refresh");
      qc.invalidateQueries({ queryKey: ["history-jobs"] });
    } catch (e: any) {
      setError(e.message || "Could not start the refresh");
    } finally {
      setStarting(false);
    }
  };

  const s = stats.data;
  const verdictTotal = s ? Object.values(s.verdicts).reduce((a, b) => a + b, 0) : 0;
  const progress = job?.result?.batch_status?.counts
    ? `batch: ${job.result.batch_status.counts.succeeded ?? 0} done`
    : job?.result?.total
      ? `${job.result.labelled ?? 0} / ${job.result.total}`
      : "";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <History className="h-5 w-5 text-primary" />
            <h1 className="text-2xl font-bold">History index</h1>
            {s && (
              <Badge className={s.mode === "on" ? "bg-emerald-500/15 text-emerald-700" : "bg-muted/50"}>
                context {s.mode}
              </Badge>
            )}
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Every chat is labelled once (what the player asked, what the agent did and promised, how
            it ended) and indexed. When a chat is graded, the grader is shown this player’s earlier
            chats and up to three similar cases that QA has ruled on — in front of the transcript, as
            reference, never as evidence. Older chats come from the raw client exports and are
            labelled without being added to the dashboards.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {running ? (
            <span className="flex items-center gap-2 text-xs text-muted-foreground">
              <Spinner className="h-3.5 w-3.5 text-primary" /> Refreshing {progress}
            </span>
          ) : writer ? (
            <Button size="sm" disabled={starting} onClick={refresh}
                    title="Label the cached chats the index lacks, regather QA verdicts, rebuild the search index">
              {starting ? <Spinner className="h-3.5 w-3.5" /> : <RefreshCw className="h-3.5 w-3.5" />}
              Refresh now
            </Button>
          ) : null}
        </div>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {job?.status === "error" && <p className="text-sm text-destructive">Last refresh failed: {job.error}</p>}

      {stats.isLoading ? (
        <div className="flex justify-center py-16"><Spinner className="h-6 w-6 text-primary" /></div>
      ) : stats.error ? (
        <p className="text-sm text-destructive">{String(stats.error)}</p>
      ) : s && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Labelled chats" value={s.labels}
                  hint={s.first ? `${fmtDate(s.first)} – ${fmtDate(s.last)} · $${s.label_cost_usd.toFixed(2)} so far` : "run a refresh to build the index"} />
            <Stat label="Players" value={s.contacts} hint="distinct players with a labelled chat" />
            <Stat label="QA verdicts" value={verdictTotal}
                  hint={`${s.verdict_chats_labelled} usable as precedents · ${Object.entries(s.verdicts).map(([k, n]) => `${k} ${n}`).join(" · ") || "none yet"}`} />
            <Stat label="Awaiting a label" value={s.unlabelled_cache}
                  hint={s.last_refresh_at ? `last refresh ${fmtDate(s.last_refresh_at)}` : "never refreshed"} />
          </div>

          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
            <Counts title="By case type" rows={s.by_case_type} />
            <Counts title="By language" rows={s.by_language} />
            <Counts title="By brand" rows={s.by_brand} />
            <Counts title="By source" rows={s.by_source} />
          </div>

          <Card className="p-4 text-xs text-muted-foreground">
            <div>Label version <code>{s.label_version}</code> · index version <code>{s.index_version}</code></div>
            <div className="mt-1">
              Raw archives found: {s.archives.length ? s.archives.join(", ") : "none"} — label them with{" "}
              <code>intercom-summary history label --source archive --batch</code>.
            </div>
            {jobs.data && jobs.data.jobs.length > 0 && (
              <div className="mt-2">
                Recent refreshes:{" "}
                {jobs.data.jobs.map((j) => (
                  <span key={j.id} className="mr-3">
                    {fmtDate(j.created_at)} · {j.status}
                    {j.result?.labelled != null && !j.result?.partial ? ` · ${j.result.labelled} labelled` : ""}
                    {j.result?.cost_usd != null ? ` · $${Number(j.result.cost_usd).toFixed(2)}` : ""}
                  </span>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
