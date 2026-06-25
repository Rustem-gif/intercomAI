import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Trash2, ChevronDown, ChevronUp, Pencil, Check, X } from "lucide-react";
import { api, IconicCase } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Button, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { fmtDate } from "@/lib/utils";

function scoreColor(score: number | null) {
  if (score == null) return "text-muted-foreground";
  if (score >= 80) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 60) return "text-amber-600 dark:text-amber-400";
  return "text-destructive";
}

function CommentEditor({
  caseItem,
  onSaved,
}: {
  caseItem: IconicCase;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(caseItem.manager_comment);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await api.put(`/api/iconic-cases/${caseItem.conversation_id}/comment`, { comment: draft });
      onSaved();
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    setDraft(caseItem.manager_comment);
    setEditing(false);
  };

  if (!editing) {
    return (
      <div className="group relative min-h-[2rem]">
        {caseItem.manager_comment ? (
          <p className="whitespace-pre-wrap text-sm text-foreground">{caseItem.manager_comment}</p>
        ) : (
          <p className="text-sm italic text-muted-foreground">No comment yet — click to add one.</p>
        )}
        <button
          className="absolute right-0 top-0 hidden rounded p-0.5 text-muted-foreground hover:text-foreground group-hover:flex"
          onClick={() => setEditing(true)}
          title="Edit comment"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <textarea
        autoFocus
        className="min-h-[80px] w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="Describe what is good or bad about this case, what supports should learn…"
      />
      <div className="flex gap-2">
        <Button size="sm" onClick={save} disabled={saving}>
          {saving ? <Spinner className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5" />}
          Save
        </Button>
        <Button size="sm" variant="ghost" onClick={cancel} disabled={saving}>
          <X className="h-3.5 w-3.5" /> Cancel
        </Button>
      </div>
    </div>
  );
}

function CaseCard({
  item,
  onRemove,
  onOpen,
  canEdit,
}: {
  item: IconicCase;
  onRemove: () => void;
  onOpen: () => void;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [removing, setRemoving] = useState(false);
  const c = item.conversation;

  const handleRemove = async () => {
    if (!confirm("Remove this case from the knowledge base?")) return;
    setRemoving(true);
    try {
      await api.delete(`/api/iconic-cases/${item.conversation_id}`);
      onRemove();
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div className="rounded-lg border bg-card shadow-sm">
      {/* Header row */}
      <div className="flex items-start gap-3 p-4">
        <div className="min-w-0 flex-1">
          <button
            className="text-left text-sm font-medium text-foreground hover:underline"
            onClick={onOpen}
          >
            {c?.subject || `#${item.conversation_id}`}
          </button>
          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
            {item.archived && (
              <span className="rounded bg-muted px-1 font-medium text-muted-foreground" title="Source conversation deleted — viewing the saved snapshot">archived</span>
            )}
            {c?.agent_name && <span>Agent: <span className="text-foreground">{c.agent_name}</span></span>}
            {c?.customer_name && <span>Customer: {c.customer_name}</span>}
            {c?.created_at && <span>{fmtDate(c.created_at)}</span>}
            {c?.score != null && (
              <span className={`font-semibold ${scoreColor(c.score)}`}>Score: {c.score}</span>
            )}
            <span className="text-muted-foreground/60">Added by {item.added_by} on {fmtDate(item.added_at)}</span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {canEdit && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground hover:text-destructive"
              onClick={handleRemove}
              disabled={removing}
              title="Remove from knowledge base"
            >
              {removing ? <Spinner className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />}
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground"
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? "Collapse" : "Expand comment"}
          >
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </Button>
        </div>
      </div>

      {/* Comment section */}
      {expanded && (
        <div className="border-t px-4 py-3">
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Manager's note</p>
          {canEdit ? (
            <CommentEditor
              caseItem={item}
              onSaved={() => qc.invalidateQueries({ queryKey: ["iconic-cases"] })}
            />
          ) : (
            <p className="whitespace-pre-wrap text-sm text-foreground">
              {item.manager_comment || <span className="italic text-muted-foreground">No comment.</span>}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function KnowledgeBase() {
  const { user } = useAuth();
  const writer = canWrite(user?.role);
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["iconic-cases"],
    queryFn: () => api.get<{ items: IconicCase[] }>("/api/iconic-cases"),
  });

  const items = data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <BookOpen className="h-5 w-5 text-primary" />
        <div>
          <h1 className="text-xl font-semibold">Knowledge Base</h1>
          <p className="text-sm text-muted-foreground">
            Iconic and representative cases curated by managers — open any conversation and click
            "Add to Knowledge Base" to include it here.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12">
          <Spinner className="h-6 w-6 text-primary" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed p-12 text-center">
          <BookOpen className="mx-auto mb-3 h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">No iconic cases yet.</p>
          <p className="mt-1 text-xs text-muted-foreground/60">
            Open a conversation and use the "Add to Knowledge Base" button to add it here.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <CaseCard
              key={item.conversation_id}
              item={item}
              canEdit={writer}
              onRemove={() => qc.invalidateQueries({ queryKey: ["iconic-cases"] })}
              onOpen={() => setOpenId(item.conversation_id)}
            />
          ))}
        </div>
      )}

      {openId && (
        <ConversationDrawer
          id={openId}
          onClose={() => setOpenId(null)}
          readOnly
          detailUrl={`/api/iconic-cases/${openId}`}
        />
      )}
    </div>
  );
}
