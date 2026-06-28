import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { api, GradeDispute } from "@/lib/api";
import { Card, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { fmtDate, scoreColor } from "@/lib/utils";

export default function Disputes() {
  const [openId, setOpenId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["grade-disputes"],
    queryFn: () => api.get<{ items: GradeDispute[] }>("/api/grade-disputes?status=open"),
  });

  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <Scale className="h-5 w-5 text-primary" />
          <h1 className="text-2xl font-bold">Grade Disputes</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Open disputes raised by agents who disagree with their QA grade. Open a row
          to accept (and re-score) or reject it.
        </p>
      </div>

      {isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Spinner className="h-6 w-6 text-primary" />
        </div>
      ) : items.length === 0 ? (
        <Card>
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            No open disputes.
          </div>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="w-32 px-4 py-2.5 font-medium">Agent</th>
                <th className="w-20 px-4 py-2.5 font-medium">Score</th>
                <th className="w-48 px-4 py-2.5 font-medium">Subject</th>
                <th className="px-4 py-2.5 font-medium">Reason</th>
                <th className="w-40 px-4 py-2.5 font-medium">Raised by</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr
                  key={d.conversation_id}
                  onClick={() => setOpenId(d.conversation_id)}
                  className="cursor-pointer border-b last:border-0 hover:bg-muted/50"
                >
                  <td className="px-4 py-3 font-medium">{d.agent_name}</td>
                  <td className={`px-4 py-3 font-semibold ${scoreColor(d.score ?? null)}`}>
                    {d.score ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    <div className="line-clamp-2">{d.subject || "(no subject)"}</div>
                  </td>
                  <td className="px-4 py-3 leading-relaxed">{d.reason}</td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {d.created_by} · {d.created_via}
                    <div>{fmtDate(d.created_at)}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {!isLoading && items.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {items.length} open dispute{items.length !== 1 ? "s" : ""}.
        </p>
      )}

      {openId && (
        <ConversationDrawer id={openId} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}
