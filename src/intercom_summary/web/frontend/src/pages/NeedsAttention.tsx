import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { api, ConversationList } from "@/lib/api";
import { Badge, Card, Input, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import CsatBadge from "@/components/CsatBadge";
import { scoreColor } from "@/lib/utils";

const LIMIT = 500;

/** The reason's group, for the filter chips ("AI: Escalation missed — …" → "AI flagged"). */
function kind(reason: string): string {
  if (reason.startsWith("AI")) return "AI flagged";
  if (reason.startsWith("Low CSAT")) return "Low CSAT";
  if (reason.startsWith("Internal note")) return "Internal note";
  return reason;
}

const KIND_STYLE: Record<string, string> = {
  "Critical fail": "border-destructive/60 bg-destructive/15 text-destructive",
  FAIL: "border-destructive/40 bg-destructive/10 text-destructive",
  "Internal note": "border-amber-500/40 bg-amber-500/10 text-amber-600",
  "AI flagged": "border-sky-500/40 bg-sky-500/10 text-sky-600",
  "Low CSAT": "border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-600",
};
const KINDS = ["Critical fail", "FAIL", "Internal note", "AI flagged", "Low CSAT"];

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export default function NeedsAttention() {
  const [openId, setOpenId] = useState<string | null>(null);
  const [since, setSince] = useState(daysAgo(7));
  const [until, setUntil] = useState("");
  const [only, setOnly] = useState<string | null>(null);

  // The QC review queue: graded chats nobody has reviewed yet that have an internal note, were
  // sent to review by the AI, failed, or got a low player rating (qa/review_queue.py).
  const params = new URLSearchParams({ review: "true", sort: "created_at", limit: String(LIMIT) });
  if (since) params.set("since", since);
  if (until) params.set("until", until + "T23:59:59");
  const { data, isLoading } = useQuery({
    queryKey: ["needs-attention", params.toString()],
    queryFn: () => api.get<ConversationList>(`/api/conversations?${params.toString()}`),
  });

  const all = data?.items ?? [];
  const counts: Record<string, number> = {};
  for (const c of all)
    for (const k of new Set((c.review_reasons ?? []).map(kind))) counts[k] = (counts[k] ?? 0) + 1;
  const rows = only ? all.filter((c) => (c.review_reasons ?? []).some((r) => kind(r) === only)) : all;

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-destructive" />
          <h1 className="text-2xl font-bold">Needs Attention</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Chats for a QC manager to check by hand: an internal note in the chat, sent to review by
          the AI, a FAIL or critical fail, or a low player rating. A chat leaves the list once a
          person confirms or corrects its grade. Click a row for the transcript and grade.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input type="date" className="h-9 w-36 px-2 text-sm" title="From date" value={since}
               max={until || undefined} onChange={(e) => setSince(e.target.value)} />
        <Input type="date" className="h-9 w-36 px-2 text-sm" title="To date" value={until}
               min={since || undefined} onChange={(e) => setUntil(e.target.value)} />
        <button
          onClick={() => setOnly(null)}
          className={`rounded-full border px-3 py-1 text-xs ${only === null ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}
        >
          All {data ? `(${data.total})` : ""}
        </button>
        {KINDS.filter((k) => counts[k]).map((k) => (
          <button
            key={k}
            onClick={() => setOnly(only === k ? null : k)}
            className={`rounded-full border px-3 py-1 text-xs ${only === k ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}
          >
            {k} ({counts[k]})
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Spinner className="h-6 w-6 text-primary" />
        </div>
      ) : rows.length === 0 ? (
        <Card>
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            Nothing needs attention in this period.
          </div>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="w-10 px-4 py-2.5 font-medium">#</th>
                <th className="w-24 px-4 py-2.5 font-medium">Date</th>
                <th className="w-20 px-4 py-2.5 font-medium">Score</th>
                <th className="w-20 px-4 py-2.5 font-medium">CSAT</th>
                <th className="w-32 px-4 py-2.5 font-medium">Agent</th>
                <th className="w-64 px-4 py-2.5 font-medium">Why</th>
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
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {c.created_at?.slice(0, 10)}
                  </td>
                  <td className={`px-4 py-3 font-bold ${scoreColor(c.score)}`}>
                    {c.score ?? "—"}
                  </td>
                  <td className="px-4 py-3">
                    <CsatBadge rating={c.csat_rating} />
                  </td>
                  <td className="px-4 py-3 font-medium">{c.agent_name}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(c.review_reasons ?? []).map((r) => (
                        <span key={r} title={r}>
                          <Badge className={KIND_STYLE[kind(r)] ?? ""}>
                            <span className="line-clamp-1 max-w-[14rem]">{r}</span>
                          </Badge>
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 leading-relaxed">
                    {c.grade_summary || (
                      <span className="italic text-muted-foreground">No summary</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {!isLoading && data && (
        <p className="text-xs text-muted-foreground">
          Showing {rows.length} of {data.total} chat{data.total !== 1 ? "s" : ""}
          {data.total > LIMIT ? ` (the newest ${LIMIT}; narrow the dates for the rest)` : ""}.
        </p>
      )}

      {openId && <ConversationDrawer id={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}
