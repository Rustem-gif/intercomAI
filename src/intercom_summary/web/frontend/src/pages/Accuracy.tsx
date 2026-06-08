import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, Cell, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, Spinner } from "@/components/ui/primitives";
import { scoreColor, fmtDate } from "@/lib/utils";
import { Lightbulb, TrendingUp, TrendingDown, Minus } from "lucide-react";

function deltaColor(d: number) {
  if (d > 5) return "text-emerald-500";
  if (d < -5) return "text-destructive";
  return "text-muted-foreground";
}

function DeltaBadge({ delta }: { delta: number }) {
  return (
    <span className={`font-semibold ${deltaColor(delta)}`}>
      {delta > 0 ? "+" : ""}{delta}
    </span>
  );
}

function BiasIndicator({ avgDelta }: { avgDelta: number }) {
  if (avgDelta > 5)
    return (
      <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400">
        <TrendingDown className="h-4 w-4" />
        <span>AI scores too low (managers raise by ~{avgDelta.toFixed(0)} pts on average)</span>
      </div>
    );
  if (avgDelta < -5)
    return (
      <div className="flex items-center gap-2 text-destructive">
        <TrendingUp className="h-4 w-4" />
        <span>AI scores too high (managers lower by ~{Math.abs(avgDelta).toFixed(0)} pts on average)</span>
      </div>
    );
  return (
    <div className="flex items-center gap-2 text-muted-foreground">
      <Minus className="h-4 w-4" />
      <span>AI scores are well-calibrated (avg delta ±{Math.abs(avgDelta).toFixed(1)} pts)</span>
    </div>
  );
}

export default function Accuracy() {
  const { data, isLoading } = useQuery({
    queryKey: ["accuracy"],
    queryFn: () => api.get<any>("/api/accuracy"),
    refetchInterval: 30_000,
  });

  if (isLoading || !data) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  const s = data.summary;
  const noData = s.total_overridden === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">AI Accuracy</h1>
        <p className="text-sm text-muted-foreground">
          Traceability of AI vs manager scoring — {s.total_overridden} override{s.total_overridden !== 1 ? "s" : ""} recorded.
        </p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-5">
            <p className="text-xs font-medium text-muted-foreground">Total graded</p>
            <p className="mt-1 text-2xl font-bold">{s.total_graded}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-5">
            <p className="text-xs font-medium text-muted-foreground">Override rate</p>
            <p className="mt-1 text-2xl font-bold">
              {s.total_overridden > 0 ? `${(s.override_rate * 100).toFixed(1)}%` : "—"}
            </p>
            <p className="text-xs text-muted-foreground">{s.total_overridden} conversation{s.total_overridden !== 1 ? "s" : ""}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-5">
            <p className="text-xs font-medium text-muted-foreground">Avg deviation</p>
            <p className={`mt-1 text-2xl font-bold ${noData ? "" : deltaColor(s.avg_delta)}`}>
              {noData ? "—" : `${s.avg_delta > 0 ? "+" : ""}${s.avg_delta}`}
            </p>
            <p className="text-xs text-muted-foreground">human − AI score</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-5">
            <p className="text-xs font-medium text-muted-foreground">Agreement rate</p>
            <p className="mt-1 text-2xl font-bold">
              {noData ? "—" : `${(s.agreement_rate * 100).toFixed(0)}%`}
            </p>
            <p className="text-xs text-muted-foreground">within ±5 pts</p>
          </CardContent>
        </Card>
      </div>

      {noData ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No overrides yet. Open a graded conversation and use the <strong>Override</strong> button (admin only) to record a manual assessment.
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Bias indicator + insights */}
          <Card>
            <CardHeader>
              <CardTitle>Calibration analysis</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <BiasIndicator avgDelta={s.avg_delta} />
              <div className="grid grid-cols-3 gap-3 text-center text-sm">
                <div className="rounded-md bg-muted p-3">
                  <p className="text-xl font-bold text-emerald-500">{s.ai_too_low}</p>
                  <p className="text-xs text-muted-foreground mt-1">AI too low (manager raised)</p>
                </div>
                <div className="rounded-md bg-muted p-3">
                  <p className="text-xl font-bold text-muted-foreground">{s.agreed_on_override}</p>
                  <p className="text-xs text-muted-foreground mt-1">Close (±5 pts)</p>
                </div>
                <div className="rounded-md bg-muted p-3">
                  <p className="text-xl font-bold text-destructive">{s.ai_too_high}</p>
                  <p className="text-xs text-muted-foreground mt-1">AI too high (manager lowered)</p>
                </div>
              </div>
              {data.insights.length > 0 && (
                <div className="space-y-2 pt-1">
                  {data.insights.map((ins: string, i: number) => (
                    <div key={i} className="flex gap-2 rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-sm">
                      <Lightbulb className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                      <span>{ins}</span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* Deviation distribution */}
            {data.deviation_distribution.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Score deviation distribution</CardTitle>
                </CardHeader>
                <CardContent className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={data.deviation_distribution} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                      <XAxis dataKey="delta" tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))"
                        tickFormatter={(v) => `${v > 0 ? "+" : ""}${v}`} />
                      <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" allowDecimals={false} />
                      <Tooltip
                        formatter={(v, _n, p) => [v, `delta ${p.payload.delta > 0 ? "+" : ""}${p.payload.delta}`]}
                        contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 }}
                      />
                      <ReferenceLine x={0} stroke="hsl(var(--muted-foreground))" strokeDasharray="3 3" />
                      <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                        {data.deviation_distribution.map((d: any, i: number) => (
                          <Cell key={i} fill={d.delta > 5 ? "#10b981" : d.delta < -5 ? "#ef4444" : "#94a3b8"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )}

            {/* Per-agent breakdown */}
            {data.agent_breakdown.length > 0 && (
              <Card>
                <CardHeader><CardTitle>By agent</CardTitle></CardHeader>
                <CardContent>
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs uppercase text-muted-foreground">
                      <tr>
                        <th className="py-2 font-medium">Agent</th>
                        <th className="py-2 text-right font-medium">Overrides</th>
                        <th className="py-2 text-right font-medium">Avg delta</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.agent_breakdown.map((a: any) => (
                        <tr key={a.agent} className="border-t">
                          <td className="py-2 font-medium">{a.agent}</td>
                          <td className="py-2 text-right text-muted-foreground">{a.overrides}</td>
                          <td className="py-2 text-right font-semibold">
                            <DeltaBadge delta={a.avg_delta} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </CardContent>
              </Card>
            )}
          </div>

          {/* Recent overrides */}
          <Card>
            <CardHeader><CardTitle>Override log</CardTitle></CardHeader>
            <CardContent>
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="py-2 font-medium">Conversation</th>
                    <th className="py-2 font-medium">Agent</th>
                    <th className="py-2 text-right font-medium">AI</th>
                    <th className="py-2 text-right font-medium">Human</th>
                    <th className="py-2 text-right font-medium">Δ</th>
                    <th className="py-2 font-medium">Reason</th>
                    <th className="py-2 text-right font-medium">By</th>
                    <th className="py-2 text-right font-medium">When</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_overrides.map((r: any) => {
                    const delta = r.human_score - r.overall_score;
                    return (
                      <tr key={r.conversation_id} className="border-t align-top">
                        <td className="py-2 font-mono text-xs text-muted-foreground">{r.conversation_id}</td>
                        <td className="py-2">{r.agent_name || "—"}</td>
                        <td className={`py-2 text-right font-semibold ${scoreColor(r.overall_score)}`}>{r.overall_score}</td>
                        <td className={`py-2 text-right font-semibold ${scoreColor(r.human_score)}`}>{r.human_score}</td>
                        <td className="py-2 text-right"><DeltaBadge delta={delta} /></td>
                        <td className="max-w-xs py-2 text-xs text-muted-foreground italic">"{r.override_reason}"</td>
                        <td className="py-2 text-right text-muted-foreground">{r.overridden_by}</td>
                        <td className="py-2 text-right text-muted-foreground">{fmtDate(r.overridden_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
