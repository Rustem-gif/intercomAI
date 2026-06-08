import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { api, ConversationList } from "@/lib/api";
import { Card, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { scoreColor } from "@/lib/utils";

export default function NeedsAttention() {
  const [openId, setOpenId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["needs-attention"],
    queryFn: () =>
      api.get<ConversationList>(
        "/api/conversations?sort=score&descending=false&limit=100"
      ),
  });

  const graded = data?.items.filter((c) => c.score !== null) ?? [];

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-destructive" />
          <h1 className="text-2xl font-bold">Needs Attention</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Conversations with the lowest QA scores — click any row to review the
          full transcript and grade.
        </p>
      </div>

      {isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Spinner className="h-6 w-6 text-primary" />
        </div>
      ) : graded.length === 0 ? (
        <Card>
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            No graded conversations yet.
          </div>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="w-10 px-4 py-2.5 font-medium">#</th>
                <th className="w-20 px-4 py-2.5 font-medium">Score</th>
                <th className="w-32 px-4 py-2.5 font-medium">Agent</th>
                <th className="w-48 px-4 py-2.5 font-medium">Subject</th>
                <th className="px-4 py-2.5 font-medium">Summary</th>
              </tr>
            </thead>
            <tbody>
              {graded.map((c, i) => (
                <tr
                  key={c.id}
                  onClick={() => setOpenId(c.id)}
                  className="cursor-pointer border-b last:border-0 hover:bg-muted/50"
                >
                  <td className="px-4 py-3 text-muted-foreground">{i + 1}</td>
                  <td className={`px-4 py-3 font-bold ${scoreColor(c.score)}`}>
                    {c.score}
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

      {!isLoading && graded.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Showing {graded.length} lowest-scored conversation
          {graded.length !== 1 ? "s" : ""}.
        </p>
      )}

      {openId && (
        <ConversationDrawer id={openId} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}
