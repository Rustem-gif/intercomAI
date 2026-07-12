import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Play,
  Square,
  RefreshCw,
  CheckCircle2,
  Clock,
  AlertCircle,
  Ban,
  Power,
} from "lucide-react";
import { api, EvalStats, Job, JobListItem, OllamaHealth, OllamaRestart } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import {
  Button,
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Spinner,
} from "@/components/ui/primitives";
import AgentMultiSelect from "@/components/AgentMultiSelect";
import { cn } from "@/lib/utils";
import { scoreColor } from "@/lib/utils";

// ── helpers ───────────────────────────────────────────────────────────────────

function pct(n: number, total: number) {
  if (!total) return 0;
  return Math.round((n / total) * 100);
}

function fmt(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    queued: { label: "Queued", cls: "border-muted-foreground text-muted-foreground" },
    running: { label: "Running", cls: "border-blue-500 text-blue-500" },
    cancelling: { label: "Cancelling…", cls: "border-orange-500 text-orange-500" },
    done: { label: "Done", cls: "border-emerald-500 text-emerald-500" },
    cancelled: { label: "Cancelled", cls: "border-orange-500 text-orange-500" },
    error: { label: "Error", cls: "border-destructive text-destructive" },
  };
  const { label, cls } = map[status] ?? { label: status, cls: "" };
  return <Badge className={cls}>{label}</Badge>;
}

function ProgressBar({ pct: p }: { pct: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-primary transition-all duration-300"
        style={{ width: `${p}%` }}
      />
    </div>
  );
}

// ── Active-job panel ──────────────────────────────────────────────────────────

function ActiveJobPanel({
  job,
  onCancel,
  onRefresh,
}: {
  job: EvalStats["active_job"];
  onCancel: () => void;
  onRefresh: () => void;
}) {
  if (!job) return null;
  const r = job.result;
  const graded = r?.graded ?? 0;
  const total = r?.total ?? 0;
  const skipped = r?.skipped ?? 0;
  const p = pct(graded, total);
  const active = ["running", "queued", "cancelling"].includes(job.status);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span className="flex items-center gap-2">
            {active && <Spinner className="h-3.5 w-3.5 text-primary" />}
            Active job
          </span>
          <div className="flex items-center gap-2">
            <StatusBadge status={job.status} />
            {job.cancellable && job.status === "running" && (
              <Button variant="destructive" size="sm" onClick={onCancel}>
                <Square className="h-3 w-3" /> Stop
              </Button>
            )}
            <button
              className="ml-1 text-muted-foreground hover:text-foreground"
              title="Refresh"
              onClick={onRefresh}
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {total > 0 && (
          <>
            <ProgressBar pct={p} />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                {graded} graded · {skipped} skipped · {total} total
              </span>
              <span>{p}%</span>
            </div>
          </>
        )}
        {job.status === "error" && (
          <p className="text-sm text-destructive">{job.error ?? "Unknown error"}</p>
        )}
        {!active && (
          <p className="text-xs text-muted-foreground">
            {job.status === "done" && "✓ Completed — stats updated."}
            {job.status === "cancelled" &&
              `Stopped after ${graded} grades. You can start a new run anytime.`}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Start-new-run form ────────────────────────────────────────────────────────

function RunForm({
  onStarted,
}: {
  onStarted: (job: Job) => void;
}) {
  const [agents, setAgents] = useState<string[]>([]);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [state, setStateVal] = useState("");
  const [regrade, setRegrade] = useState(false);
  const [backend, setBackend] = useState("ollama");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const start = async () => {
    setLoading(true);
    setError("");
    try {
      const j = await api.post<Job>("/api/review", {
        agents: agents.length ? agents : null,
        since: since || null,
        until: until || null,
        state: state || null,
        regrade,
        backend,
      });
      onStarted(j);
    } catch (e: any) {
      setError(e.message || "Failed to start evaluation");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Start evaluation</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <label className="mb-1.5 block text-sm font-medium">Agents</label>
          <AgentMultiSelect value={agents} onChange={setAgents} />
          <p className="mt-1 text-xs text-muted-foreground">
            Leave empty to evaluate all agents.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1.5 block text-sm font-medium">Since</label>
            <Input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">Until</label>
            <Input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
          </div>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium">Conversation state</label>
          <select
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
            value={state}
            onChange={(e) => setStateVal(e.target.value)}
          >
            <option value="">Any</option>
            <option value="open">Open</option>
            <option value="closed">Closed</option>
            <option value="snoozed">Snoozed</option>
          </select>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium">Grading engine</label>
          <select
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
            value={backend}
            onChange={(e) => setBackend(e.target.value)}
          >
            <option value="ollama">Qwen (local · Ollama)</option>
            <option value="api">Claude API</option>
          </select>
        </div>

        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={regrade}
            onChange={(e) => setRegrade(e.target.checked)}
            className="h-4 w-4 rounded border-input"
          />
          Re-grade already-evaluated conversations
        </label>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <Button className="w-full" onClick={start} disabled={loading}>
          {loading ? <Spinner className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          {loading ? "Starting…" : "Run evaluation"}
        </Button>
      </CardContent>
    </Card>
  );
}

// ── Job history table ─────────────────────────────────────────────────────────

function JobHistory({ jobs }: { jobs: JobListItem[] }) {
  if (!jobs.length) {
    return <p className="text-sm text-muted-foreground">No evaluation runs yet.</p>;
  }
  return (
    <div className="overflow-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50">
          <tr>
            {["Started", "Engine", "Graded", "Skipped", "Total", "Status"].map((h) => (
              <th key={h} className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {jobs.map((j) => {
            const r = j.result;
            const engineMap: Record<string, string> = {
              ollama: "Qwen",
              api: "Claude API",
              claude_code: "Claude Code",
            };
            const engine = engineMap[(j as any).params?.backend ?? ""] ?? "—";
            return (
              <tr key={j.id} className="hover:bg-muted/30">
                <td className="px-4 py-2.5 text-xs text-muted-foreground">{fmt(j.created_at)}</td>
                <td className="px-4 py-2.5 text-xs text-muted-foreground">{engine}</td>
                <td className="px-4 py-2.5 font-medium">{r?.graded ?? "—"}</td>
                <td className="px-4 py-2.5 text-muted-foreground">{r?.skipped ?? "—"}</td>
                <td className="px-4 py-2.5 text-muted-foreground">{r?.total ?? "—"}</td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={j.status} />
                  {j.status === "error" && (
                    <p className="mt-0.5 text-xs text-destructive">{j.error}</p>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  accent,
}: {
  icon: React.ElementType;
  label: string;
  value: React.ReactNode;
  sub?: string;
  accent?: string;
}) {
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-medium text-muted-foreground">{label}</p>
            <p className={cn("mt-1 text-2xl font-bold", accent)}>{value}</p>
            {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
          </div>
          <Icon className="h-5 w-5 text-muted-foreground/50" />
        </div>
      </CardContent>
    </Card>
  );
}

// ── Ollama service panel ──────────────────────────────────────────────────────

function OllamaPanel({ writer }: { writer: boolean }) {
  const { data: health, refetch } = useQuery<OllamaHealth>({
    queryKey: ["ollamaHealth"],
    queryFn: () => api.get("/api/ollama/health"),
    // Poll faster while down so the card recovers promptly after a restart.
    refetchInterval: (q) => (q.state.data?.reachable === false ? 4000 : 30000),
  });

  const [restarting, setRestarting] = useState(false);
  const [msg, setMsg] = useState("");

  const reachable = health?.reachable;
  // Don't show anything until the first health check resolves.
  if (health === undefined) return null;

  // When the server is healthy, keep the panel minimal — only surface a control
  // when it's down (the crash case) or for writers who may want to recycle it.
  if (reachable && !writer) return null;

  const restart = async () => {
    if (!confirm("Restart the Ollama service? Any in-flight grade will be interrupted.")) return;
    setRestarting(true);
    setMsg("");
    try {
      const r = await api.post<OllamaRestart>("/api/ollama/restart");
      setMsg(
        r.reachable
          ? "Ollama is back online."
          : "Restart requested, but the server hasn't responded yet — give it a moment.",
      );
      refetch();
    } catch (e: any) {
      setMsg(e.message || "Failed to restart Ollama.");
    } finally {
      setRestarting(false);
    }
  };

  return (
    <Card className={cn(!reachable && "border-destructive")}>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span className="flex items-center gap-2">
            <span
              className={cn(
                "inline-block h-2.5 w-2.5 rounded-full",
                reachable ? "bg-emerald-500" : "bg-destructive",
              )}
            />
            Ollama service
          </span>
          <Badge className={reachable ? "border-emerald-500 text-emerald-500" : "border-destructive text-destructive"}>
            {reachable ? "Online" : "Offline"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {reachable ? (
          <p className="text-xs text-muted-foreground">
            Local grading model reachable
            {health?.models?.length ? ` · ${health.models.length} model(s) loaded` : ""}.
          </p>
        ) : (
          <p className="text-sm text-destructive">
            The local Ollama server is not responding{health?.error ? ` (${health.error})` : ""}.
            Grading will fail until it is restarted.
          </p>
        )}
        {writer && (
          <Button
            variant={reachable ? "outline" : "destructive"}
            size="sm"
            onClick={restart}
            disabled={restarting}
          >
            {restarting ? <Spinner className="h-3.5 w-3.5" /> : <Power className="h-3.5 w-3.5" />}
            {restarting ? "Restarting…" : "Restart Ollama"}
          </Button>
        )}
        {msg && <p className="text-xs text-muted-foreground">{msg}</p>}
      </CardContent>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Evaluation() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const writer = canWrite(user?.role);

  const { data: stats, refetch: refetchStats } = useQuery<EvalStats>({
    queryKey: ["evalStats"],
    queryFn: () => api.get("/api/evaluation/stats"),
    refetchInterval: (q) => {
      const status = q.state.data?.active_job?.status;
      return status && ["running", "queued", "cancelling"].includes(status) ? 1500 : false;
    },
  });

  const { data: jobsData, refetch: refetchJobs } = useQuery<{ jobs: JobListItem[] }>({
    queryKey: ["evalJobs"],
    queryFn: () => api.get("/api/jobs?kind=review&limit=25"),
    refetchInterval: stats?.active_job?.status === "running" ? 5000 : false,
  });

  // When the active job finishes, invalidate overview + conversations caches.
  const prevStatus = stats?.active_job?.status;
  useEffect(() => {
    if (prevStatus && !["running", "queued", "cancelling"].includes(prevStatus)) {
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      refetchJobs();
    }
  }, [prevStatus]);

  const handleStarted = (_job: Job) => {
    refetchStats();
    refetchJobs();
  };

  const handleCancel = async () => {
    const jobId = stats?.active_job?.id;
    if (!jobId) return;
    try {
      await api.post(`/api/jobs/${jobId}/cancel`);
      refetchStats();
    } catch (e: any) {
      alert(e.message || "Could not cancel job");
    }
  };

  const total = stats?.total ?? 0;
  const graded = stats?.graded ?? 0;
  const pending = stats?.pending ?? 0;
  const stale = stats?.stale ?? 0;
  const wrongRuleset = stats?.wrong_ruleset ?? 0;
  const ignored = stats?.ignored ?? 0;
  const coverage = pct(graded, total);
  const activeJob = stats?.active_job ?? null;
  const isActive =
    activeJob && ["running", "queued", "cancelling"].includes(activeJob.status);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">Evaluation</h1>
        <p className="text-sm text-muted-foreground">
          Grade cached conversations. Each conversation is graded against its assigned agent's
          ruleset — VIP agents use the VIP ruleset.
        </p>
      </div>

      {/* Conversations graded before their agent joined a different group. They were graded
          correctly at the time, so they are deliberately NOT re-graded automatically; converting
          them is an explicit decision. */}
      {wrongRuleset > 0 && (
        <div className="rounded-md border border-amber-500/50 bg-amber-500/5 p-3 text-sm">
          <span className="font-medium text-amber-600 dark:text-amber-400">
            {wrongRuleset.toLocaleString()} graded with a different ruleset
          </span>
          <span className="ml-1 text-muted-foreground">
            than their agent's group uses today (typically a VIP agent's history from before they
            joined VIP). They are left as they were graded. To re-score them with the current
            ruleset, run a review over those agents with “re-grade” enabled.
          </span>
        </div>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard
          icon={MessagesIcon}
          label="Conversations"
          value={total.toLocaleString()}
          sub={ignored > 0 ? `${ignored.toLocaleString()} ignored by tag` : "gradeable"}
        />
        <StatCard
          icon={CheckCircle2}
          label="Graded"
          value={graded.toLocaleString()}
          accent="text-emerald-500"
          sub={
            stale > 0
              ? `${coverage}% coverage · ${stale.toLocaleString()} on an older ruleset`
              : `${coverage}% coverage`
          }
        />
        <StatCard
          icon={Clock}
          label="Pending"
          value={pending.toLocaleString()}
          accent={pending > 0 ? "text-amber-500" : undefined}
        />
        <StatCard
          icon={isActive ? SpinnerIcon : ActivityIcon}
          label="Status"
          value={isActive ? "Running" : graded === total && total > 0 ? "Up to date" : "Idle"}
          accent={
            isActive
              ? "text-blue-500"
              : graded === total && total > 0
              ? "text-emerald-500"
              : undefined
          }
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        {/* Left column: active job + history */}
        <div className="space-y-6">
          <OllamaPanel writer={writer} />

          {activeJob && (
            <ActiveJobPanel
              job={activeJob}
              onCancel={handleCancel}
              onRefresh={() => refetchStats()}
            />
          )}

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                Recent runs
                <button
                  className="text-muted-foreground hover:text-foreground"
                  onClick={() => refetchJobs()}
                  title="Refresh history"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </button>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {jobsData ? (
                <JobHistory jobs={jobsData.jobs.filter((j) => j.kind === "review")} />
              ) : (
                <Spinner className="h-5 w-5" />
              )}
            </CardContent>
          </Card>
        </div>

        {/* Right column: start form */}
        {writer && (
          <div>
            {isActive ? (
              <Card>
                <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
                  <Spinner className="h-6 w-6 text-primary" />
                  <p className="text-sm font-medium">Evaluation in progress</p>
                  <p className="text-xs text-muted-foreground">
                    You can stop it at any time — progress so far is saved.
                  </p>
                  {activeJob?.cancellable && (
                    <Button variant="destructive" size="sm" onClick={handleCancel}>
                      <Square className="h-3 w-3" /> Stop evaluation
                    </Button>
                  )}
                </CardContent>
              </Card>
            ) : (
              <RunForm onStarted={handleStarted} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── tiny icon wrappers ────────────────────────────────────────────────────────
function MessagesIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}
function ActivityIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  );
}
function SpinnerIcon({ className }: { className?: string }) {
  return <Spinner className={className} />;
}
