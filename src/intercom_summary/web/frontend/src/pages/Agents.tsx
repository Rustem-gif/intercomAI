import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bar, BarChart, Cell, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis, CartesianGrid,
} from "recharts";
import { api, AgentScores } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, Spinner } from "@/components/ui/primitives";
import { Button } from "@/components/ui/primitives";
import { scoreColor } from "@/lib/utils";
import { Link2, ChevronDown, ChevronUp, Crown } from "lucide-react";
import GenerateLinkModal from "@/components/GenerateLinkModal";
import { useAuth } from "@/lib/auth";
import { useGroup, GROUP_LABELS } from "@/lib/group";

function barColor(score: number) {
  if (score >= 85) return "#10b981";
  if (score >= 70) return "#f59e0b";
  return "#ef4444";
}

function lineColor(score: number) {
  if (score >= 85) return "#10b981";
  if (score >= 70) return "#f59e0b";
  return "#ef4444";
}

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default function Agents() {
  // Default to the last 30 days, matching the previous "Month" default.
  const [start, setStart] = useState<string>(isoDaysAgo(30));
  const [end, setEnd] = useState<string>(new Date().toISOString().slice(0, 10));
  const invalidRange = !!start && !!end && start > end;

  const { data, isLoading } = useQuery({
    queryKey: ["agent-scores", start, end],
    queryFn: () => {
      const params = new URLSearchParams();
      if (start) params.set("start", start);
      if (end) params.set("end", end);
      const qs = params.toString();
      return api.get<AgentScores>(`/api/agents/scores${qs ? `?${qs}` : ""}`);
    },
    enabled: !invalidRange,
  });
  const [linkAgent, setLinkAgent] = useState<string | null>(null);
  const [trendAgent, setTrendAgent] = useState<string | null>(null);

  const board = data?.agents ?? [];

  // VIP membership. Moving an agent into the VIP group changes the ruleset their future
  // conversations are graded against, so only admins may do it.
  const qc = useQueryClient();
  const { user } = useAuth();
  const { group } = useGroup();
  const isAdmin = user?.role === "admin";
  const { data: groupsData } = useQuery({
    queryKey: ["agent-groups"],
    queryFn: () => api.get<{ groups: Record<string, string> }>("/api/agent-groups"),
  });
  const groups = groupsData?.groups ?? {};
  const [savingAgent, setSavingAgent] = useState<string | null>(null);

  const toggleVip = async (agent: string, isVip: boolean) => {
    setSavingAgent(agent);
    try {
      await api.put("/api/agent-groups", {
        agent_name: agent,
        group_id: isVip ? "standard" : "vip",
      });
      // Membership changes what the group switcher resolves to, so refetch everything scoped.
      qc.invalidateQueries();
    } finally {
      setSavingAgent(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">
            Agents
            {group !== "all" && (
              <span className="ml-2 align-middle text-sm font-normal text-muted-foreground">
                · {GROUP_LABELS[group]}
              </span>
            )}
          </h1>
          <p className="text-sm text-muted-foreground">
            Average QA score per support agent (by conversation date, override-aware).
            {group === "all" && " VIP and standard agents are graded against different rulesets, so their scores are not directly comparable."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs">
            <label className="text-muted-foreground" htmlFor="agents-start">From</label>
            <input
              id="agents-start"
              type="date"
              value={start}
              max={end || undefined}
              onChange={(e) => setStart(e.target.value)}
              className="bg-transparent text-xs font-medium outline-none"
            />
            <span className="text-muted-foreground">–</span>
            <label className="text-muted-foreground" htmlFor="agents-end">To</label>
            <input
              id="agents-end"
              type="date"
              value={end}
              min={start || undefined}
              onChange={(e) => setEnd(e.target.value)}
              className="bg-transparent text-xs font-medium outline-none"
            />
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="text-xs"
            onClick={() => {
              setStart("");
              setEnd("");
            }}
          >
            All time
          </Button>
        </div>
      </div>
      {invalidRange && (
        <p className="text-xs text-destructive">
          The “From” date must be on or before the “To” date.
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Average score by agent</CardTitle>
        </CardHeader>
        <CardContent className="h-72">
          {isLoading ? (
            <div className="flex h-full items-center justify-center">
              <Spinner className="h-6 w-6 text-primary" />
            </div>
          ) : board.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={board} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <XAxis dataKey="agent" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" />
                <Tooltip
                  contentStyle={{
                    background: "hsl(var(--card))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="avg_score" radius={[4, 4, 0, 0]}>
                  {board.map((a, i) => (
                    <Cell key={i} fill={barColor(a.avg_score)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              No graded conversations in this period.
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Breakdown</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="py-2 font-medium">Agent</th>
                <th className="py-2 font-medium">Group</th>
                <th className="py-2 font-medium">Graded</th>
                <th className="py-2 text-right font-medium">Avg score</th>
                <th className="py-2 text-right font-medium">Avg CSAT</th>
                <th className="py-2 text-right font-medium">Low CSAT</th>
                <th className="py-2 text-right font-medium">Trend</th>
                <th className="py-2 text-right font-medium">Review link</th>
              </tr>
            </thead>
            <tbody>
              {board.map((a) => (
                <>
                  <tr key={a.agent} className="border-t">
                    <td className="py-2 font-medium">{a.agent}</td>
                    <td className="py-2">
                      {(() => {
                        const isVip = groups[a.agent] === "vip";
                        if (!isAdmin) {
                          return isVip ? (
                            <span className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
                              <Crown className="h-3 w-3" /> VIP
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground">Standard</span>
                          );
                        }
                        return (
                          <Button
                            variant={isVip ? "secondary" : "ghost"}
                            size="sm"
                            disabled={savingAgent === a.agent}
                            title={
                              isVip
                                ? "Move back to the standard group (future conversations use the standard ruleset)"
                                : "Move to the VIP group (future conversations use the VIP ruleset)"
                            }
                            onClick={() => toggleVip(a.agent, isVip)}
                          >
                            <Crown
                              className={`h-3.5 w-3.5 ${isVip ? "text-amber-500" : "text-muted-foreground"}`}
                            />
                            <span className="text-xs">{isVip ? "VIP" : "Standard"}</span>
                          </Button>
                        );
                      })()}
                    </td>
                    <td className="py-2 text-muted-foreground">{a.count}</td>
                    <td className={`py-2 text-right font-semibold ${scoreColor(a.avg_score)}`}>{a.avg_score}</td>
                    <td className="py-2 text-right tabular-nums text-muted-foreground">
                      {a.avg_csat != null ? `${a.avg_csat}/5` : "—"}
                    </td>
                    <td className={`py-2 text-right tabular-nums font-semibold ${a.low_csat_count > 0 ? "text-destructive" : "text-muted-foreground"}`}>
                      {a.low_csat_count}
                    </td>
                    <td className="py-2 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        title="Show score trend"
                        onClick={() => setTrendAgent(trendAgent === a.agent ? null : a.agent)}
                      >
                        {trendAgent === a.agent ? (
                          <ChevronUp className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronDown className="h-3.5 w-3.5" />
                        )}
                      </Button>
                    </td>
                    <td className="py-2 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        title="Generate shareable review link"
                        onClick={() => setLinkAgent(a.agent)}
                      >
                        <Link2 className="h-3.5 w-3.5" />
                      </Button>
                    </td>
                  </tr>
                  {trendAgent === a.agent && (
                    <tr key={`${a.agent}-trend`} className="border-t bg-muted/30">
                      <td colSpan={8} className="px-2 py-3">
                        <AgentTrendChart agent={a.agent} avgScore={a.avg_score} />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {linkAgent && (
        <GenerateLinkModal agentName={linkAgent} onClose={() => setLinkAgent(null)} />
      )}
    </div>
  );
}

function AgentTrendChart({ agent, avgScore }: { agent: string; avgScore: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["agent-trend", agent],
    queryFn: () => api.get<{ trend: { day: string; avg_score: number; count: number }[] }>(
      `/api/agents/trend?agent=${encodeURIComponent(agent)}`
    ),
  });

  if (isLoading) {
    return (
      <div className="flex h-24 items-center justify-center">
        <Spinner className="h-4 w-4 text-primary" />
      </div>
    );
  }

  const trend = data?.trend ?? [];

  if (trend.length < 2) {
    return (
      <p className="py-4 text-center text-xs text-muted-foreground">
        Not enough grading history to show a trend.
      </p>
    );
  }

  const color = lineColor(avgScore);

  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground">
        Score history for <span className="font-semibold text-foreground">{agent}</span>
        {" "}({trend.length} grading day{trend.length !== 1 ? "s" : ""})
      </p>
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={trend} margin={{ top: 4, right: 8, left: -28, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="day"
            tick={{ fontSize: 10 }}
            stroke="hsl(var(--muted-foreground))"
            tickFormatter={(v) => v.slice(5)}
          />
          <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" />
          <Tooltip
            contentStyle={{
              background: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 6,
              fontSize: 11,
            }}
            formatter={(v: number) => [`${v}`, "Avg score"]}
            labelFormatter={(l) => `Date: ${l}`}
          />
          <Line
            type="monotone"
            dataKey="avg_score"
            stroke={color}
            strokeWidth={2}
            dot={{ fill: color, r: 3 }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
