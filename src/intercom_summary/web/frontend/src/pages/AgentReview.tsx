import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ConversationRow, ReviewPortal, CoachingItem } from "@/lib/api";
import { Spinner, Button } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { scoreColor, fmtDate } from "@/lib/utils";
import {
  AlertTriangle, CheckCircle2, Circle, GraduationCap,
  CalendarClock, MessageSquare, BookMarked,
} from "lucide-react";

export default function AgentReview() {
  const { token } = useParams<{ token: string }>();
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const [openKbId, setOpenKbId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["review-portal", token],
    queryFn: () => api.get<ReviewPortal>(`/api/review/${token}`),
    retry: false,
  });

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
            This link is invalid or has expired. Please ask your manager to generate a new one.
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      {data.mode === "coaching" ? (
        <CoachingPortal data={data} token={token!} onOpenConv={setOpenId} />
      ) : (
        <ReviewPortalView data={data} token={token!} onOpenConv={setOpenId} />
      )}

      <KnowledgeBaseSection token={token!} onOpen={setOpenKbId} />

      {openId && (
        <ConversationDrawer
          id={openId}
          onClose={() => setOpenId(null)}
          readOnly
          detailUrl={`/api/review/${token}/conversations/${openId}`}
          disputeUrl={`/api/review/${token}/conversations/${openId}/csat-dispute`}
        />
      )}

      {openKbId && (
        <ConversationDrawer
          id={openKbId}
          onClose={() => setOpenKbId(null)}
          readOnly
          detailUrl={`/api/review/${token}/iconic-cases/${openKbId}`}
        />
      )}
    </>
  );
}

// ── Knowledge base exemplars (read-only, survive conversation deletion) ──────────
interface AgentIconicCase {
  conversation_id: string;
  manager_comment: string;
  added_at: string;
  conversation: {
    id: string; agent_name: string; customer_name: string; subject: string;
    state: string; created_at: string; score: number | null;
  } | null;
}

function KnowledgeBaseSection({ token, onOpen }: { token: string; onOpen: (id: string) => void }) {
  const { data } = useQuery({
    queryKey: ["review-portal-kb", token],
    queryFn: () => api.get<{ items: AgentIconicCase[]; total: number }>(
      `/api/review/${token}/iconic-cases`
    ),
    retry: false,
  });

  const items = data?.items ?? [];
  if (items.length === 0) return null;

  return (
    <div className="mx-auto max-w-3xl px-4 pb-10">
      <div className="mb-2 flex items-center gap-2">
        <BookMarked className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">Knowledge base — example chats</h2>
        <span className="text-xs text-muted-foreground">({items.length})</span>
      </div>
      <p className="mb-3 text-xs text-muted-foreground">
        Reviewed and re-scored chats your manager saved as examples. Available any time.
      </p>
      <div className="space-y-2">
        {items.map((it) => {
          const c = it.conversation;
          return (
            <button
              key={it.conversation_id}
              onClick={() => onOpen(it.conversation_id)}
              className="block w-full rounded-lg border bg-card p-3 text-left shadow-sm hover:border-primary/50"
            >
              <div className="text-sm font-medium">{c?.subject || `#${it.conversation_id}`}</div>
              <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                {c?.created_at && <span>{fmtDate(c.created_at)}</span>}
                {c?.score != null && (
                  <span className={`font-semibold ${scoreColor(c.score)}`}>Score: {c.score}</span>
                )}
              </div>
              {it.manager_comment && (
                <p className="mt-1 text-xs italic text-muted-foreground">"{it.manager_comment}"</p>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── Coaching portal ────────────────────────────────────────────────────────────
function CoachingPortal({
  data, token, onOpenConv,
}: {
  data: ReviewPortal;
  token: string;
  onOpenConv: (id: string) => void;
}) {
  const qc = useQueryClient();
  const session = data.session!;
  const isDone = session.status === "done";

  const { data: ackData } = useQuery({
    queryKey: ["review-acks", token],
    queryFn: () =>
      api.get<{ acknowledged_ids: string[] }>(`/api/review/${token}/acknowledgments`),
  });

  const ackMutation = useMutation({
    mutationFn: (convId: string) =>
      api.post(`/api/review/${token}/conversations/${convId}/acknowledge`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review-acks", token] }),
  });

  const finishMutation = useMutation({
    mutationFn: () => api.post(`/api/review/${token}/finish`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review-portal", token] }),
  });

  const acknowledgedIds = new Set(ackData?.acknowledged_ids ?? []);
  const viewedCount = acknowledgedIds.size;
  const total = data.items.length;
  const allViewed = total > 0 && viewedCount >= total;

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className={`border-b px-6 py-5 ${isDone ? "bg-emerald-50 dark:bg-emerald-950/20" : "bg-card"}`}>
        <div className="mx-auto max-w-3xl">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${isDone ? "bg-emerald-500" : "bg-primary"}`}>
                <GraduationCap className="h-5 w-5 text-white" />
              </div>
              <div>
                <h1 className="text-lg font-bold">{session.title}</h1>
                <p className="text-sm text-muted-foreground">
                  Coaching session for{" "}
                  <span className="font-medium text-foreground">{data.agent_name}</span>
                  {" · "}
                  {isDone ? (
                    <span className="font-medium text-emerald-600">Completed</span>
                  ) : (
                    <span>{total} conversation{total !== 1 ? "s" : ""} to review</span>
                  )}
                </p>
              </div>
            </div>

            {!isDone && (
              <Button
                variant={allViewed ? "default" : "outline"}
                onClick={() => {
                  if (!confirm("Mark this coaching session as complete? Your manager will see it as done.")) return;
                  finishMutation.mutate();
                }}
                disabled={finishMutation.isPending}
              >
                {finishMutation.isPending ? (
                  <Spinner className="h-4 w-4" />
                ) : (
                  <CheckCircle2 className="h-4 w-4" />
                )}
                Finish Coaching
              </Button>
            )}

            {isDone && (
              <div className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-3 py-1.5 text-sm font-medium text-emerald-600">
                <CheckCircle2 className="h-4 w-4" />
                Coaching complete
              </div>
            )}
          </div>

          {/* Due date */}
          {session.due_date && (
            <div className="mt-3 flex items-center gap-1.5 text-sm text-muted-foreground">
              <CalendarClock className="h-4 w-4" />
              Due by <span className="font-medium text-foreground">{fmtDate(session.due_date)}</span>
            </div>
          )}

          {/* Manager notes */}
          {session.notes && (
            <div className="mt-3 rounded-lg border border-primary/20 bg-primary/5 px-4 py-3">
              <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-primary">
                <MessageSquare className="h-3.5 w-3.5" />
                Manager's notes
              </div>
              <p className="text-sm text-foreground">{session.notes}</p>
            </div>
          )}

          {/* Progress bar */}
          {total > 0 && !isDone && (
            <div className="mt-4">
              <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                <span>Progress</span>
                <span>{viewedCount}/{total} reviewed</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-all"
                  style={{ width: `${total > 0 ? (viewedCount / total) * 100 : 0}%` }}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Conversation list */}
      <div className="mx-auto max-w-3xl px-4 py-6">
        {data.items.length === 0 ? (
          <p className="text-center text-sm text-muted-foreground py-12">
            No conversations in this coaching session yet.
          </p>
        ) : (
          <div className="space-y-3">
            {data.items.map((item: CoachingItem) => {
              const c = item.conversation;
              const isAcked = acknowledgedIds.has(item.conversation_id);
              return (
                <div
                  key={item.conversation_id}
                  className={`rounded-xl border bg-card transition-opacity ${isAcked ? "opacity-60" : ""}`}
                >
                  <div
                    className="flex cursor-pointer items-start gap-3 p-4 hover:bg-muted/30"
                    onClick={() => onOpenConv(item.conversation_id)}
                  >
                    <BookMarked className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-medium">{c?.subject || "(no subject)"}</span>
                        {c?.score != null && (
                          <span className={`shrink-0 text-sm font-semibold ${scoreColor(c.score)}`}>
                            {c.score}
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {c?.customer_name || "—"} · {fmtDate(c?.created_at ?? "")}
                      </div>
                      {/* Per-item manager note */}
                      {item.note && (
                        <div className="mt-2 rounded-md border-l-2 border-primary/40 bg-primary/5 px-3 py-1.5 text-xs text-foreground">
                          <span className="font-semibold text-primary">Manager: </span>
                          {item.note}
                        </div>
                      )}
                    </div>
                    {/* Acknowledge button */}
                    <button
                      className="shrink-0 p-1"
                      title={isAcked ? "Mark as unread" : "Mark as reviewed"}
                      onClick={(e) => {
                        e.stopPropagation();
                        ackMutation.mutate(item.conversation_id);
                      }}
                    >
                      {isAcked ? (
                        <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                      ) : (
                        <Circle className="h-5 w-5 text-muted-foreground/40 hover:text-emerald-400 transition-colors" />
                      )}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Plain review portal (unchanged behaviour) ─────────────────────────────────
function ReviewPortalView({
  data, token, onOpenConv,
}: {
  data: ReviewPortal;
  token: string;
  onOpenConv: (id: string) => void;
}) {
  const qc = useQueryClient();

  const { data: ackData } = useQuery({
    queryKey: ["review-acks", token],
    queryFn: () =>
      api.get<{ acknowledged_ids: string[] }>(`/api/review/${token}/acknowledgments`),
    enabled: !!data,
  });

  const ackMutation = useMutation({
    mutationFn: (convId: string) =>
      api.post<{ acknowledged: boolean }>(`/api/review/${token}/conversations/${convId}/acknowledge`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review-acks", token] }),
  });

  const acknowledgedIds = new Set(ackData?.acknowledged_ids ?? []);
  const viewedCount = acknowledgedIds.size;
  const totalCount = data.total;

  return (
    <div className="min-h-screen bg-background">
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
                      onClick={() => onOpenConv(c.id)}
                      className={`cursor-pointer border-t transition-colors hover:bg-muted/40 ${isAcked ? "opacity-60" : ""}`}
                    >
                      <td
                        className="px-4 py-3"
                        onClick={(e) => { e.stopPropagation(); ackMutation.mutate(c.id); }}
                      >
                        {isAcked ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                        ) : (
                          <Circle className="h-4 w-4 text-muted-foreground/40 hover:text-emerald-400 transition-colors" />
                        )}
                      </td>
                      <td className="px-4 py-3 tabular-nums text-muted-foreground">{fmtDate(c.created_at)}</td>
                      <td className="px-4 py-3 font-medium">{c.customer_name || "—"}</td>
                      <td className="max-w-xs truncate px-4 py-3 text-muted-foreground">{c.subject || "(no subject)"}</td>
                      <td className="px-4 py-3">
                        {c.custom_tags?.split(",").filter(Boolean).map((t) => (
                          <span key={t} className="mr-1 inline-block rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{t}</span>
                        ))}
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
    </div>
  );
}
