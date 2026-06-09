import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ConversationRow, ReviewPortal } from "@/lib/api";
import { Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { scoreColor, fmtDate } from "@/lib/utils";
import { AlertTriangle, CheckCircle2, Circle } from "lucide-react";

export default function AgentReview() {
  const { token } = useParams<{ token: string }>();
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["review-portal", token],
    queryFn: () => api.get<ReviewPortal>(`/api/review/${token}`),
    retry: false,
  });

  const { data: ackData } = useQuery({
    queryKey: ["review-acks", token],
    queryFn: () => api.get<{ acknowledged_ids: string[] }>(`/api/review/${token}/acknowledgments`),
    enabled: !!data,
  });

  const ackMutation = useMutation({
    mutationFn: (convId: string) =>
      api.post<{ acknowledged: boolean }>(`/api/review/${token}/conversations/${convId}/acknowledge`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review-acks", token] }),
  });

  const acknowledgedIds = new Set(ackData?.acknowledged_ids ?? []);
  const viewedCount = acknowledgedIds.size;
  const totalCount = data?.total ?? 0;

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex h-screen items-center justify-center p-6">
        <div className="flex max-w-sm flex-col items-center gap-3 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
            <AlertTriangle className="h-6 w-6 text-destructive" />
          </div>
          <h1 className="text-lg font-semibold">Link unavailable</h1>
          <p className="text-sm text-muted-foreground">
            This review link is invalid or has expired. Please ask your manager to generate a new one.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Minimal header */}
      <div className="border-b bg-card px-6 py-4">
        <h1 className="text-lg font-bold">{data.label}</h1>
        <div className="mt-0.5 flex flex-wrap items-center gap-3">
          <p className="text-sm text-muted-foreground">
            Conversations for{" "}
            <span className="font-medium text-foreground">{data.agent_name}</span>
            {data.tag ? ` · tagged "${data.tag}"` : ""}
          </p>
          {totalCount > 0 && (
            <span className={`flex items-center gap-1 text-sm font-medium ${viewedCount === totalCount ? "text-emerald-600" : "text-muted-foreground"}`}>
              <CheckCircle2 className="h-4 w-4" />
              {viewedCount}/{totalCount} reviewed
            </span>
          )}
        </div>
      </div>

      {/* Conversation list */}
      <div className="mx-auto max-w-5xl px-4 py-6">
        {data.conversations.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
            <p className="text-sm">No conversations found for this review.</p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-4 py-3 font-medium">Reviewed</th>
                  <th className="px-4 py-3 font-medium">Date</th>
                  <th className="px-4 py-3 font-medium">Customer</th>
                  <th className="px-4 py-3 font-medium">Subject</th>
                  <th className="px-4 py-3 font-medium">Tags</th>
                  <th className="px-4 py-3 text-right font-medium">Score</th>
                </tr>
              </thead>
              <tbody>
                {data.conversations.map((c: ConversationRow) => {
                  const isAcked = acknowledgedIds.has(c.id);
                  return (
                    <tr
                      key={c.id}
                      onClick={() => setOpenId(c.id)}
                      className={`cursor-pointer border-t transition-colors hover:bg-muted/40 ${isAcked ? "opacity-60" : ""}`}
                    >
                      <td
                        className="px-4 py-3"
                        onClick={(e) => {
                          e.stopPropagation();
                          ackMutation.mutate(c.id);
                        }}
                      >
                        {isAcked ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        ) : (
                          <Circle className="h-4 w-4 text-muted-foreground/40 hover:text-emerald-400 transition-colors" />
                        )}
                      </td>
                      <td className="px-4 py-3 tabular-nums text-muted-foreground">
                        {fmtDate(c.created_at)}
                      </td>
                      <td className="px-4 py-3 font-medium">{c.customer_name || "—"}</td>
                      <td className="max-w-xs truncate px-4 py-3 text-muted-foreground">
                        {c.subject || "(no subject)"}
                      </td>
                      <td className="px-4 py-3">
                        {c.custom_tags
                          ? c.custom_tags
                              .split(",")
                              .filter(Boolean)
                              .map((t) => (
                                <span
                                  key={t}
                                  className="mr-1 inline-block rounded-full border px-2 py-0.5 text-xs text-muted-foreground"
                                >
                                  {t}
                                </span>
                              ))
                          : null}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {c.score != null ? (
                          <span className={`font-semibold ${scoreColor(c.score)}`}>{c.score}</span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Read-only conversation drawer */}
      {openId && (
        <ConversationDrawer
          id={openId}
          onClose={() => setOpenId(null)}
          readOnly
          detailUrl={`/api/review/${token}/conversations/${openId}`}
        />
      )}
    </div>
  );
}
