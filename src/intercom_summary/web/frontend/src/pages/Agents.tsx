import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, Overview } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, Spinner } from "@/components/ui/primitives";
import { Button } from "@/components/ui/primitives";
import { scoreColor } from "@/lib/utils";
import { Link2 } from "lucide-react";
import GenerateLinkModal from "@/components/GenerateLinkModal";

function barColor(score: number) {
  if (score >= 85) return "#10b981";
  if (score >= 70) return "#f59e0b";
  return "#ef4444";
}

export default function Agents() {
  const { data, isLoading } = useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/api/overview") });
  const [linkAgent, setLinkAgent] = useState<string | null>(null);

  if (isLoading || !data) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  const board = data.agent_leaderboard;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Agents</h1>
        <p className="text-sm text-muted-foreground">Average QA score per support agent.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Average score by agent</CardTitle>
        </CardHeader>
        <CardContent className="h-72">
          {board.length ? (
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
              No graded conversations yet.
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
                <th className="py-2 font-medium">Graded</th>
                <th className="py-2 text-right font-medium">Avg score</th>
                <th className="py-2 text-right font-medium">Review link</th>
              </tr>
            </thead>
            <tbody>
              {board.map((a) => (
                <tr key={a.agent} className="border-t">
                  <td className="py-2 font-medium">{a.agent}</td>
                  <td className="py-2 text-muted-foreground">{a.count}</td>
                  <td className={`py-2 text-right font-semibold ${scoreColor(a.avg_score)}`}>{a.avg_score}</td>
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
