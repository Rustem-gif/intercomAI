import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Download, RefreshCw, Sparkles, Wrench } from "lucide-react";
import { useGroup, GROUP_LABELS } from "@/lib/group";
import { api, Job, Overview as OverviewData } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Button, Card, CardContent, CardHeader, CardTitle, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import RunDialog from "@/components/RunDialog";
import { scoreColor } from "@/lib/utils";

function Kpi({ label, value, accent }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <Card>
      <CardContent className="pt-5">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        <p className={`mt-1 text-2xl font-bold ${accent ?? ""}`}>{value}</p>
      </CardContent>
    </Card>
  );
}

export default function Overview() {
  const { user } = useAuth();
  const { group } = useGroup();
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<null | "fetch" | "review">(null);
  const [qaAllJob, setQaAllJob] = useState<Job | null>(null);
  const [repairJob, setRepairJob] = useState<Job | null>(null);

  useEffect(() => {
    if (!repairJob || ["done", "error"].includes(repairJob.status)) return;
    const t = setInterval(async () => {
      const j = await api.get<Job>(`/api/jobs/${repairJob.id}`);
      setRepairJob(j);
      if (j.status === "done") {
        qc.invalidateQueries({ queryKey: ["overview"] });
        qc.invalidateQueries({ queryKey: ["conversations"] });
        qc.invalidateQueries({ queryKey: ["agents"] });
      }
    }, 2000);
    return () => clearInterval(t);
  }, [repairJob]);

  const runRepair = async () => {
    try {
      const j = await api.post<Job>("/api/repair/agent-names");
      setRepairJob(j);
    } catch (e: any) {
      alert(e.message || "Failed to start repair");
    }
  };

  // Poll the "grade all" background job until it finishes.
  useEffect(() => {
    if (!qaAllJob || ["done", "error"].includes(qaAllJob.status)) return;
    const t = setInterval(async () => {
      const j = await api.get<Job>(`/api/jobs/${qaAllJob.id}`);
      setQaAllJob(j);
      if (j.status === "done") {
        qc.invalidateQueries({ queryKey: ["overview"] });
        qc.invalidateQueries({ queryKey: ["conversations"] });
      }
    }, 1500);
    return () => clearInterval(t);
  }, [qaAllJob]);

  const runQaAll = async () => {
    try {
      const j = await api.post<Job>("/api/review", { agents: null, regrade: false });
      setQaAllJob(j);
    } catch (e: any) {
      alert(e.message || "Failed to start QA");
    }
  };

  const [openId, setOpenId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({ queryKey: ["overview"], queryFn: () => api.get<OverviewData>("/api/overview") });

  if (isLoading || !data) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  const k = data.kpis;
  const writer = canWrite(user?.role);

  return (
    <div className="space-y-6">
      {/* Header + actions */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">
            Overview
            {group !== "all" && (
              <span className="ml-2 align-middle text-sm font-normal text-muted-foreground">
                · {GROUP_LABELS[group]}
              </span>
            )}
          </h1>
          <p className="text-sm text-muted-foreground">
            Support QA at a glance.
            {group === "vip" && " Scored against the VIP ruleset."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Inline repair status */}
          {repairJob && (
            <span className="flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-xs text-muted-foreground">
              {repairJob.status === "running" || repairJob.status === "queued" ? (
                <><Spinner className="h-3 w-3" /> Fixing agent names…</>
              ) : repairJob.status === "done" ? (
                <span className="text-emerald-500">
                  ✓ Fixed {repairJob.result?.fixed ?? 0} / {repairJob.result?.total ?? 0}
                </span>
              ) : (
                <span className="text-destructive">Repair failed — {repairJob.error}</span>
              )}
              <button className="ml-1 hover:text-foreground" onClick={() => setRepairJob(null)}>×</button>
            </span>
          )}
          {/* Inline QA-all status */}
          {qaAllJob && (
            <span className="flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-xs text-muted-foreground">
              {qaAllJob.status === "running" || qaAllJob.status === "queued" ? (
                <><Spinner className="h-3 w-3" /> Grading all…</>
              ) : qaAllJob.status === "done" ? (
                <span className="text-emerald-500">
                  ✓ Graded {qaAllJob.result?.graded ?? 0}, skipped {qaAllJob.result?.skipped ?? 0}
                </span>
              ) : (
                <span className="text-destructive">QA failed — {qaAllJob.error}</span>
              )}
              <button className="ml-1 hover:text-foreground" onClick={() => setQaAllJob(null)}>×</button>
            </span>
          )}
          <a href="/api/export/qa.xlsx">
            <Button variant="outline" size="sm">
              <Download className="h-4 w-4" /> QA report
            </Button>
          </a>
          {writer && (
            <>
              <Button variant="outline" size="sm" onClick={() => setDialog("review")}>
                <Sparkles className="h-4 w-4" /> Run QA
              </Button>
              <Button
                size="sm"
                onClick={runQaAll}
                disabled={!!qaAllJob && !["done", "error"].includes(qaAllJob.status)}
              >
                <Sparkles className="h-4 w-4" /> Grade all
              </Button>
              <Button variant="outline" size="sm" onClick={() => setDialog("fetch")}>
                <RefreshCw className="h-4 w-4" /> Fetch
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={runRepair}
                disabled={!!repairJob && !["done", "error"].includes(repairJob.status)}
                title="Re-fetch conversations to fix missing agent names"
              >
                <Wrench className="h-4 w-4" /> Fix agent names
              </Button>
            </>
          )}
        </div>
      </div>

      {/* KPI stats bar */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Kpi label="Conversations" value={k.conversations} />
        <Kpi label="Graded" value={k.graded} />
        <Kpi label="Avg score" value={k.avg_score} accent={scoreColor(k.avg_score)} />
        <Kpi label="Violations" value={k.violations} />
        <Kpi label="Agents" value={k.agents} />
      </div>

      {/* Bento grid */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Score trend — spans 2 cols */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Score trend</CardTitle>
          </CardHeader>
          <CardContent className="h-64">
            {data.score_trend.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data.score_trend} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                  <Tooltip
                    contentStyle={{
                      background: "hsl(var(--card))",
                      border: "1px solid hsl(var(--border))",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Line type="monotone" dataKey="avg_score" stroke="hsl(var(--primary))" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <Empty>No grades yet. Run QA to see trends.</Empty>
            )}
          </CardContent>
        </Card>

        {/* Top violations */}
        <Card>
          <CardHeader>
            <CardTitle>Top violations</CardTitle>
          </CardHeader>
          <CardContent>
            {data.top_violations.length ? (
              <ul className="space-y-2">
                {data.top_violations.map((v, i) => (
                  <li key={i} className="flex items-start justify-between gap-2 text-sm">
                    <span className="text-muted-foreground">{v.text}</span>
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs font-medium">{v.count}×</span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>None recorded.</Empty>
            )}
          </CardContent>
        </Card>

        {/* Agent leaderboard */}
        <Card>
          <CardHeader>
            <CardTitle>Agent leaderboard</CardTitle>
          </CardHeader>
          <CardContent>
            {data.agent_leaderboard.length ? (
              <ul className="space-y-2">
                {data.agent_leaderboard.map((a) => (
                  <li key={a.agent} className="flex items-center justify-between text-sm">
                    <span className="font-medium">{a.agent}</span>
                    <span className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">{a.count}</span>
                      <span className={`font-semibold ${scoreColor(a.avg_score)}`}>{a.avg_score}</span>
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No agents graded.</Empty>
            )}
          </CardContent>
        </Card>

        {/* Worst conversations — spans 2 cols */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Needs attention (lowest scores)</CardTitle>
              <Link
                to="/needs-attention"
                className="text-xs text-primary hover:underline"
              >
                View all →
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            {data.worst_conversations.length ? (
              <ul className="divide-y">
                {data.worst_conversations.map((c) => (
                  <li
                    key={c.id}
                    onClick={() => setOpenId(c.id)}
                    className="flex cursor-pointer items-center gap-3 rounded py-2 text-sm hover:bg-muted/50"
                  >
                    <span className={`w-10 shrink-0 font-bold ${scoreColor(c.score)}`}>{c.score}</span>
                    <span className="w-20 shrink-0 truncate text-muted-foreground">{c.agent}</span>
                    <span className="min-w-0 flex-1 truncate">{c.summary}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>Nothing graded yet.</Empty>
            )}
          </CardContent>
        </Card>
      </div>

      {dialog && (
        <RunDialog
          kind={dialog}
          onClose={() => setDialog(null)}
          onPartialData={() => {
            qc.invalidateQueries({ queryKey: ["conversations"] });
          }}
          onDone={() => {
            qc.invalidateQueries({ queryKey: ["overview"] });
            qc.invalidateQueries({ queryKey: ["conversations"] });
          }}
        />
      )}

      {openId && <ConversationDrawer id={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center py-8 text-sm text-muted-foreground">{children}</div>;
}
