import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, CoachingSession, CoachingItem } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Button, Card, CardContent, CardHeader, CardTitle, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { scoreColor, fmtDate } from "@/lib/utils";
import {
  Plus, Pencil, Trash2, Check, X, ChevronDown, ChevronRight,
  BookMarked, CircleDot, CircleCheck, Link2, Copy, CheckCheck,
} from "lucide-react";

export default function Coaching() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const writer = canWrite(user?.role);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [openConvId, setOpenConvId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [sharingId, setSharingId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["coaching"],
    queryFn: () => api.get<{ items: CoachingSession[] }>("/api/coaching"),
  });

  const { data: detailData } = useQuery({
    queryKey: ["coaching-detail", expandedId],
    queryFn: () => api.get<CoachingSession>(`/api/coaching/${expandedId}`),
    enabled: !!expandedId,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/api/coaching/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["coaching"] });
      if (expandedId) setExpandedId(null);
    },
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.put(`/api/coaching/${id}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["coaching"] });
      qc.invalidateQueries({ queryKey: ["coaching-detail", expandedId] });
    },
  });

  const removeItemMutation = useMutation({
    mutationFn: ({ sessionId, convId }: { sessionId: string; convId: string }) =>
      api.delete(`/api/coaching/${sessionId}/items/${convId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["coaching-detail", expandedId] }),
  });

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  const sessions = data?.items ?? [];
  const openSessions = sessions.filter((s) => s.status === "open");
  const doneSessions = sessions.filter((s) => s.status === "done");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Coaching</h1>
          <p className="text-sm text-muted-foreground">Group conversations into coaching sessions for targeted feedback.</p>
        </div>
        {writer && (
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" /> New Session
          </Button>
        )}
      </div>

      {creating && (
        <SessionForm
          onSave={async (values) => {
            await api.post("/api/coaching", values);
            qc.invalidateQueries({ queryKey: ["coaching"] });
            setCreating(false);
          }}
          onCancel={() => setCreating(false)}
        />
      )}

      {sessions.length === 0 && !creating ? (
        <Card>
          <CardContent className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            No coaching sessions yet. Create one to group conversations for an agent.
          </CardContent>
        </Card>
      ) : (
        <>
          {openSessions.length > 0 && (
            <SessionGroup
              label="Open"
              sessions={openSessions}
              expandedId={expandedId}
              detailData={detailData}
              editingId={editingId}
              sharingId={sharingId}
              writer={writer}
              onExpand={(id) => setExpandedId(expandedId === id ? null : id)}
              onEdit={(id) => setEditingId(id)}
              onCancelEdit={() => setEditingId(null)}
              onSaveEdit={async (id, values) => {
                await api.put(`/api/coaching/${id}`, values);
                qc.invalidateQueries({ queryKey: ["coaching"] });
                qc.invalidateQueries({ queryKey: ["coaching-detail", id] });
                setEditingId(null);
              }}
              onDelete={(id) => { if (confirm("Delete this coaching session?")) deleteMutation.mutate(id); }}
              onToggleStatus={(id, status) => statusMutation.mutate({ id, status })}
              onShare={(id) => setSharingId(sharingId === id ? null : id)}
              onOpenConv={(id) => setOpenConvId(id)}
              onRemoveItem={(sessionId, convId) => removeItemMutation.mutate({ sessionId, convId })}
            />
          )}

          {doneSessions.length > 0 && (
            <SessionGroup
              label="Done"
              sessions={doneSessions}
              expandedId={expandedId}
              detailData={detailData}
              editingId={editingId}
              sharingId={sharingId}
              writer={writer}
              onExpand={(id) => setExpandedId(expandedId === id ? null : id)}
              onEdit={(id) => setEditingId(id)}
              onCancelEdit={() => setEditingId(null)}
              onSaveEdit={async (id, values) => {
                await api.put(`/api/coaching/${id}`, values);
                qc.invalidateQueries({ queryKey: ["coaching"] });
                qc.invalidateQueries({ queryKey: ["coaching-detail", id] });
                setEditingId(null);
              }}
              onDelete={(id) => { if (confirm("Delete this coaching session?")) deleteMutation.mutate(id); }}
              onToggleStatus={(id, status) => statusMutation.mutate({ id, status })}
              onShare={(id) => setSharingId(sharingId === id ? null : id)}
              onOpenConv={(id) => setOpenConvId(id)}
              onRemoveItem={(sessionId, convId) => removeItemMutation.mutate({ sessionId, convId })}
            />
          )}
        </>
      )}

      {openConvId && (
        <ConversationDrawer id={openConvId} onClose={() => setOpenConvId(null)} />
      )}
    </div>
  );
}

// ── Session group (Open / Done) ────────────────────────────────────────────────
function SessionGroup({
  label, sessions, expandedId, detailData, editingId, sharingId, writer,
  onExpand, onEdit, onCancelEdit, onSaveEdit, onDelete, onToggleStatus, onShare, onOpenConv, onRemoveItem,
}: {
  label: string;
  sessions: CoachingSession[];
  expandedId: string | null;
  detailData: CoachingSession | undefined;
  editingId: string | null;
  sharingId: string | null;
  writer: boolean;
  onExpand: (id: string) => void;
  onEdit: (id: string) => void;
  onCancelEdit: () => void;
  onSaveEdit: (id: string, values: any) => Promise<void>;
  onDelete: (id: string) => void;
  onToggleStatus: (id: string, status: string) => void;
  onShare: (id: string) => void;
  onOpenConv: (id: string) => void;
  onRemoveItem: (sessionId: string, convId: string) => void;
}) {
  return (
    <div className="space-y-2">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</h2>
      {sessions.map((session) => (
        <Card key={session.id} className="overflow-hidden">
          <div
            className="flex cursor-pointer items-center gap-3 px-5 py-4 hover:bg-muted/30"
            onClick={() => onExpand(session.id)}
          >
            {expandedId === session.id ? (
              <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{session.title}</span>
                <span className="text-xs text-muted-foreground">· {session.agent_name}</span>
                {session.due_date && (
                  <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-600">
                    due {fmtDate(session.due_date)}
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {session.item_count ?? 0} conversation{session.item_count !== 1 ? "s" : ""} · created by {session.created_by} · {fmtDate(session.created_at)}
              </div>
            </div>
            {writer && (
              <div className="flex shrink-0 items-center gap-1" onClick={(e) => e.stopPropagation()}>
                <Button
                  variant="ghost"
                  size="sm"
                  title={session.status === "open" ? "Mark done" : "Reopen"}
                  onClick={() => onToggleStatus(session.id, session.status === "open" ? "done" : "open")}
                >
                  {session.status === "open" ? (
                    <CircleCheck className="h-4 w-4 text-emerald-500" />
                  ) : (
                    <CircleDot className="h-4 w-4 text-muted-foreground" />
                  )}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  title="Share coaching link"
                  onClick={() => onShare(session.id)}
                >
                  <Link2 className={`h-3.5 w-3.5 ${sharingId === session.id ? "text-primary" : ""}`} />
                </Button>
                <Button variant="ghost" size="icon" onClick={() => onEdit(session.id)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button variant="ghost" size="icon" onClick={() => onDelete(session.id)}>
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </Button>
              </div>
            )}
          </div>

          {sharingId === session.id && (
            <div className="border-t px-5 py-3 bg-muted/30" onClick={(e) => e.stopPropagation()}>
              <ShareCoachingLink session={session} />
            </div>
          )}

          {expandedId === session.id && (
            <div className="border-t px-5 pb-5 pt-4">
              {editingId === session.id ? (
                <SessionForm
                  initial={session}
                  onSave={(values) => onSaveEdit(session.id, values)}
                  onCancel={onCancelEdit}
                />
              ) : (
                <>
                  {session.notes && (
                    <p className="mb-4 text-sm text-muted-foreground">{session.notes}</p>
                  )}
                  <SessionItems
                    session={session}
                    detailData={detailData}
                    writer={writer}
                    onOpenConv={onOpenConv}
                    onRemoveItem={onRemoveItem}
                  />
                </>
              )}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}

// ── Conversation items inside an expanded session ─────────────────────────────
function SessionItems({
  session, detailData, writer, onOpenConv, onRemoveItem,
}: {
  session: CoachingSession;
  detailData: CoachingSession | undefined;
  writer: boolean;
  onOpenConv: (id: string) => void;
  onRemoveItem: (sessionId: string, convId: string) => void;
}) {
  const items: CoachingItem[] = detailData?.id === session.id ? (detailData.items ?? []) : [];

  if (!detailData || detailData.id !== session.id) {
    return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner className="h-4 w-4" /> Loading…</div>;
  }

  if (!items.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No conversations yet. Open a conversation and use "Add to coaching session".
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Conversations ({items.length})
      </h4>
      {items.map((item) => {
        const c = item.conversation;
        return (
          <div
            key={item.conversation_id}
            className="flex items-center gap-3 rounded-md border px-3 py-2.5 text-sm"
          >
            <BookMarked className="h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <button
                className="truncate font-medium text-left hover:underline"
                onClick={() => onOpenConv(item.conversation_id)}
              >
                {c?.subject || item.conversation_id}
              </button>
              <div className="text-xs text-muted-foreground">
                {c?.agent_name ?? "?"} · {fmtDate(c?.created_at ?? "")}
                {c?.score != null && (
                  <span className={`ml-2 font-semibold ${scoreColor(c.score)}`}>{c.score}</span>
                )}
              </div>
              {item.note && <p className="mt-0.5 text-xs text-muted-foreground italic">{item.note}</p>}
            </div>
            {writer && (
              <Button
                variant="ghost"
                size="icon"
                title="Remove from session"
                onClick={() => onRemoveItem(session.id, item.conversation_id)}
              >
                <X className="h-3.5 w-3.5 text-muted-foreground" />
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Share coaching link panel ─────────────────────────────────────────────────
function ShareCoachingLink({ session }: { session: CoachingSession }) {
  const [generatedUrl, setGeneratedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const res = await api.post<{ token: string }>("/api/agent-links", {
        agent_name: session.agent_name,
        label: session.title,
        session_id: session.id,
        expires_in_days: null,
      });
      const url = `${window.location.origin}/review/${res.token}`;
      setGeneratedUrl(url);
    } catch (e: any) {
      setError(e?.message ?? "Failed to generate link");
    } finally {
      setGenerating(false);
    }
  };

  const copy = () => {
    if (!generatedUrl) return;
    navigator.clipboard.writeText(generatedUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Share coaching link with {session.agent_name}
      </p>
      {!generatedUrl ? (
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={generate} disabled={generating}>
            {generating ? <Spinner className="h-3.5 w-3.5" /> : <Link2 className="h-3.5 w-3.5" />}
            Generate link
          </Button>
          {error && <span className="text-xs text-destructive">{error}</span>}
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <input
            readOnly
            value={generatedUrl}
            className="flex-1 rounded-md border bg-background px-3 py-1.5 text-xs font-mono text-muted-foreground outline-none"
            onFocus={(e) => e.target.select()}
          />
          <Button size="sm" variant="outline" onClick={copy}>
            {copied ? <CheckCheck className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? "Copied!" : "Copy"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setGeneratedUrl(null)}>
            New link
          </Button>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        The agent opens this link — no login needed. They'll see the conversations, manager notes, and a "Finish Coaching" button.
      </p>
    </div>
  );
}

// ── Create / edit form ─────────────────────────────────────────────────────────
function SessionForm({
  initial,
  onSave,
  onCancel,
}: {
  initial?: CoachingSession;
  onSave: (values: any) => Promise<void>;
  onCancel: () => void;
}) {
  const [agentName, setAgentName] = useState(initial?.agent_name ?? "");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [dueDate, setDueDate] = useState(initial?.due_date ?? "");
  const [saving, setSaving] = useState(false);

  const { data: agentsData } = useQuery({
    queryKey: ["agents"],
    queryFn: () => api.get<{ agents: string[] }>("/api/agents"),
  });

  const handleSave = async () => {
    if (!title.trim()) return;
    if (!initial && !agentName.trim()) return;
    setSaving(true);
    try {
      await onSave({
        agent_name: agentName,
        title: title.trim(),
        notes: notes.trim(),
        due_date: dueDate || null,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="border-primary/30">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{initial ? "Edit session" : "New coaching session"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {!initial && (
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Agent</label>
            {agentsData?.agents.length ? (
              <select
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
              >
                <option value="">Select agent…</option>
                {agentsData.agents.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            ) : (
              <input
                type="text"
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                placeholder="Agent name…"
                className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
              />
            )}
          </div>
        )}
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Title</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. June tone issues"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Notes</label>
          <textarea
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="What should the agent focus on?"
            className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Due date (optional)</label>
          <input
            type="date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
            className="rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
          />
        </div>
        <div className="flex gap-2">
          <Button onClick={handleSave} disabled={saving || !title.trim()}>
            {saving ? <Spinner className="h-4 w-4" /> : <Check className="h-4 w-4" />}
            {initial ? "Save changes" : "Create session"}
          </Button>
          <Button variant="outline" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
