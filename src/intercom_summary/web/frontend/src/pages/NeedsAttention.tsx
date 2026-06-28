import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { api, ConversationList, ConversationRow } from "@/lib/api";
import { Badge, Card, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import CsatBadge from "@/components/CsatBadge";
import { scoreColor, isLowCsat, CSAT_LOW_MAX } from "@/lib/utils";

export default function NeedsAttention() {
  const [openId, setOpenId] = useState<string | null>(null);

  const lowScore = useQuery({
    queryKey: ["needs-attention", "score"],
    queryFn: () =>
      api.get<ConversationList>(
        "/api/conversations?sort=score&descending=false&limit=100"
      ),
  });

  const lowCsat = useQuery({
    queryKey: ["needs-attention", "csat"],
    queryFn: () =>
      api.get<ConversationList>(
        `/api/conversations?max_csat=${CSAT_LOW_MAX}&limit=100`
      ),
  });

  const isLoading = lowScore.isLoading || lowCsat.isLoading;

  // Merge the two populations, dedupe by id. A row qualifies for the list if it has a
  // QA score (lowest-first) and/or a low CSAT rating.
  const byId = new Map<string, ConversationRow>();
  for (const c of lowScore.data?.items ?? []) {
    if (c.score !== null) byId.set(c.id, c);
  }
  for (const c of lowCsat.data?.items ?? []) {
    if (!byId.has(c.id)) byId.set(c.id, c);
  }
  const rows = Array.from(byId.values()).sort((a, b) => {
    // Low-CSAT chats float to the top, then by ascending QA score.
    const aLow = isLowCsat(a.csat_rating) ? 0 : 1;
    const bLow = isLowCsat(b.csat_rating) ? 0 : 1;
    if (aLow !== bLow) return aLow - bLow;
    return (a.score ?? 999) - (b.score ?? 999);
  });

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-destructive" />
          <h1 className="text-2xl font-bold">Needs Attention</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Conversations with the lowest QA scores or a low customer satisfaction
          rating — click any row to review the full transcript and grade.
        </p>
      </div>

      {isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Spinner className="h-6 w-6 text-primary" />
        </div>
      ) : rows.length === 0 ? (
        <Card>
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            Nothing needs attention right now.
          </div>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="w-10 px-4 py-2.5 font-medium">#</th>
                <th className="w-20 px-4 py-2.5 font-medium">Score</th>
                <th className="w-20 px-4 py-2.5 font-medium">CSAT</th>
                <th className="w-32 px-4 py-2.5 font-medium">Agent</th>
                <th className="w-48 px-4 py-2.5 font-medium">Subject</th>
                <th className="px-4 py-2.5 font-medium">Summary</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c, i) => (
                <tr
                  key={c.id}
                  onClick={() => setOpenId(c.id)}
                  className="cursor-pointer border-b last:border-0 hover:bg-muted/50"
                >
                  <td className="px-4 py-3 text-muted-foreground">{i + 1}</td>
                  <td className={`px-4 py-3 font-bold ${scoreColor(c.score)}`}>
                    {c.score ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <CsatBadge rating={c.csat_rating} />
                  </td>
                  <td className="px-4 py-3 font-medium">{c.agent_name}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    <div className="line-clamp-2">
                      {c.subject || "(no subject)"}
                    </div>
                    <div className="text-xs opacity-70">
                      {c.customer_name || c.customer_email}
                    </div>
                  </td>
                  <td className="px-4 py-3 leading-relaxed">
                    {isLowCsat(c.csat_rating) && (
                      <Badge className="mb-1 mr-2 border-destructive/40 bg-destructive/10 text-destructive">
                        needs attention — low CSAT
                      </Badge>
                    )}
                    {c.grade_summary || (
                      <span className="italic text-muted-foreground">
                        No summary
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {!isLoading && rows.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Showing {rows.length} conversation{rows.length !== 1 ? "s" : ""} that
          need attention.
        </p>
      )}

      {openId && (
        <ConversationDrawer id={openId} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}
