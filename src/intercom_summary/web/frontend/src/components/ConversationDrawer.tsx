import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ConversationDetail, CoachingSession } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Button, Spinner } from "./ui/primitives";
import GradePanel from "./GradePanel";
import AiChatPanel from "./AiChatPanel";
import TagEditor from "./TagEditor";
import { X, Bot, ClipboardList, BookOpen, BookMarked, GraduationCap, Check } from "lucide-react";
import { fmtDate, fmtTime, fmtGap, gapSeconds } from "@/lib/utils";

type RightPanel = "grade" | "chat";

interface DrawerProps {
  id: string;
  onClose: () => void;
  readOnly?: boolean;
  /** Override the detail fetch URL. Defaults to /api/conversations/:id */
  detailUrl?: string;
}

export default function ConversationDrawer({ id, onClose, readOnly = false, detailUrl }: DrawerProps) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const writer = !readOnly && canWrite(user?.role);
  const [rightPanel, setRightPanel] = useState<RightPanel>("grade");
  const [savingTags, setSavingTags] = useState(false);
  const [togglingIconic, setTogglingIconic] = useState(false);
  const [coachingOpen, setCoachingOpen] = useState(false);
  const [addingToSession, setAddingToSession] = useState<string | null>(null);

  const { data: coachingSessions } = useQuery({
    queryKey: ["coaching"],
    queryFn: () => api.get<{ items: CoachingSession[] }>("/api/coaching"),
    enabled: writer && coachingOpen,
  });

  const fetchUrl = detailUrl ?? `/api/conversations/${id}`;
  const { data, isLoading } = useQuery({
    queryKey: ["conversation", fetchUrl],
    queryFn: () => api.get<ConversationDetail>(fetchUrl),
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40">
      <div className="flex h-full w-full max-w-4xl flex-col bg-background shadow-xl">
        <div className="flex h-14 shrink-0 items-center justify-between border-b px-5">
          <h2 className="font-semibold">Conversation {id}</h2>
          <div className="flex items-center gap-1">
            {writer && data && (
              <Button
                variant={data.iconic ? "default" : "outline"}
                size="sm"
                disabled={togglingIconic}
                title={data.iconic ? "Remove from Knowledge Base" : "Add to Knowledge Base"}
                onClick={async () => {
                  setTogglingIconic(true);
                  try {
                    if (data.iconic) {
                      await api.delete(`/api/iconic-cases/${id}`);
                    } else {
                      await api.post("/api/iconic-cases", { conversation_id: id, comment: "" });
                    }
                    qc.invalidateQueries({ queryKey: ["conversation", id] });
                    qc.invalidateQueries({ queryKey: ["iconic-cases"] });
                  } finally {
                    setTogglingIconic(false);
                  }
                }}
              >
                {togglingIconic ? (
                  <Spinner className="h-3.5 w-3.5" />
                ) : data.iconic ? (
                  <BookMarked className="h-3.5 w-3.5" />
                ) : (
                  <BookOpen className="h-3.5 w-3.5" />
                )}
                <span className="hidden sm:inline">
                  {data.iconic ? "In Knowledge Base" : "Add to Knowledge Base"}
                </span>
              </Button>
            )}
            {writer && (
              <div className="relative">
                <Button
                  variant="outline"
                  size="sm"
                  title="Add to coaching session"
                  onClick={() => setCoachingOpen((v) => !v)}
                >
                  <GraduationCap className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Coaching</span>
                </Button>
                {coachingOpen && (
                  <div className="absolute right-0 top-full z-20 mt-1 w-64 rounded-md border bg-card p-2 shadow-lg">
                    <p className="mb-1.5 px-1 text-xs font-medium text-muted-foreground">Add to session:</p>
                    {!coachingSessions?.items.length ? (
                      <p className="px-1 text-xs text-muted-foreground">No open sessions. Create one on the Coaching page.</p>
                    ) : (
                      coachingSessions.items
                        .filter((s) => s.status === "open")
                        .map((s) => (
                          <button
                            key={s.id}
                            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-muted"
                            onClick={async () => {
                              setAddingToSession(s.id);
                              try {
                                await api.post(`/api/coaching/${s.id}/items`, { conversation_id: id, note: "" });
                                qc.invalidateQueries({ queryKey: ["coaching-detail", s.id] });
                              } finally {
                                setAddingToSession(null);
                                setCoachingOpen(false);
                              }
                            }}
                          >
                            {addingToSession === s.id ? (
                              <Spinner className="h-3 w-3 shrink-0" />
                            ) : (
                              <Check className="h-3 w-3 shrink-0 opacity-0" />
                            )}
                            <span className="truncate">{s.title}</span>
                            <span className="ml-auto shrink-0 text-xs text-muted-foreground">{s.agent_name}</span>
                          </button>
                        ))
                    )}
                  </div>
                )}
              </div>
            )}
            <Button variant="ghost" size="icon" onClick={onClose}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {isLoading || !data ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner className="h-6 w-6 text-primary" />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1">
            {/* Transcript */}
            <div className="min-w-0 flex-1 overflow-auto border-r p-5">
              <div className="mb-3 text-sm text-muted-foreground">
                <div className="font-medium text-foreground">{data.conversation.subject || "(no subject)"}</div>
                <div>
                  {data.conversation.assignee?.name ?? "?"} ↔ {data.conversation.contact?.name ?? "?"} ·{" "}
                  {data.conversation.state} · {fmtDate(data.conversation.created_at)}
                </div>
                {data.sla && (
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                    <span>
                      First response:{" "}
                      <span className={data.sla.first_response_breached ? "font-semibold text-destructive" : "font-semibold text-emerald-600"}>
                        {data.sla.first_response_time_human}
                      </span>
                      <span className="opacity-60"> / target {fmtGap(data.sla.first_response_target)}</span>
                    </span>
                    <span>
                      Time to close: <span className="font-semibold text-foreground">{data.sla.time_to_close_human}</span>
                    </span>
                  </div>
                )}
                {/* Intercom tags (read-only) */}
                {data.conversation.tags?.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {data.conversation.tags.map((t: string) => (
                      <span key={t} className="rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {/* Custom tags */}
              {!readOnly && (
                <div className="mb-4">
                  <p className="mb-1 text-xs font-medium text-muted-foreground">Custom tags</p>
                  <TagEditor
                    tags={
                      (data.conversation as any).custom_tags
                        ? (data.conversation as any).custom_tags.split(",").filter(Boolean)
                        : []
                    }
                    disabled={!writer || savingTags}
                    onChange={async (tags) => {
                      setSavingTags(true);
                      try {
                        await api.put(`/api/conversations/${id}/tags`, { tags });
                        qc.invalidateQueries({ queryKey: ["conversation", id] });
                        qc.invalidateQueries({ queryKey: ["conversations"] });
                        qc.invalidateQueries({ queryKey: ["tags"] });
                      } finally {
                        setSavingTags(false);
                      }
                    }}
                  />
                </div>
              )}
              <div className="space-y-3">
                {data.conversation.messages.map((m: any, i: number) => {
                  const agent = m.author_type === "admin";
                  const prev = i > 0 ? data.conversation.messages[i - 1] : null;
                  const gap = gapSeconds(prev?.created_at, m.created_at);
                  // First agent reply is held to the first-response target; later ones to follow-up.
                  const isFirstAgent =
                    agent && data.conversation.messages.findIndex((x: any) => x.author_type === "admin") === i;
                  const target = isFirstAgent
                    ? data.sla?.first_response_target ?? 120
                    : data.sla?.followup_target ?? 300;
                  const slowReply = agent && gap != null && gap > target;
                  return (
                    <div key={i} className={`flex ${agent ? "justify-end" : "justify-start"}`}>
                      <div
                        className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                          agent ? "bg-primary text-primary-foreground" : "bg-muted"
                        }`}
                      >
                        <div className="mb-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs opacity-70">
                          <span>
                            {m.author_name} {m.part_type && m.part_type !== "comment" ? `· ${m.part_type}` : ""}
                          </span>
                          {m.created_at && <span className="tabular-nums">{fmtTime(m.created_at)}</span>}
                          {slowReply && (
                            <span className="rounded bg-destructive px-1.5 py-0.5 font-medium text-destructive-foreground opacity-100">
                              ⏱ waited {fmtGap(gap)}
                            </span>
                          )}
                        </div>
                        {m.text || <span className="italic opacity-60">(no text)</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Right panel — Grade or AI chat */}
            <div className="flex w-96 shrink-0 flex-col bg-card">
              {/* Tab switcher — hidden in read-only mode (only grade tab available) */}
              {!readOnly && (
                <div className="flex shrink-0 border-b">
                  <button
                    onClick={() => setRightPanel("grade")}
                    className={`flex flex-1 items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition-colors ${
                      rightPanel === "grade"
                        ? "border-b-2 border-primary text-primary"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <ClipboardList className="h-3.5 w-3.5" />
                    Grade
                  </button>
                  <button
                    onClick={() => setRightPanel("chat")}
                    className={`flex flex-1 items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition-colors ${
                      rightPanel === "chat"
                        ? "border-b-2 border-primary text-primary"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Bot className="h-3.5 w-3.5" />
                    Ask Qwen
                  </button>
                </div>
              )}

              <div className="min-h-0 flex-1 overflow-hidden">
                {(readOnly || rightPanel === "grade") ? (
                  <div className="h-full overflow-auto">
                    <GradePanel
                      grade={data.grade}
                      conversationId={id}
                      canOverride={!readOnly && user?.role === "admin"}
                      onOverridden={() => {
                        qc.invalidateQueries({ queryKey: ["conversation", id] });
                        qc.invalidateQueries({ queryKey: ["conversations"] });
                        qc.invalidateQueries({ queryKey: ["accuracy"] });
                      }}
                    />
                  </div>
                ) : (
                  <AiChatPanel conversationId={id} />
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
