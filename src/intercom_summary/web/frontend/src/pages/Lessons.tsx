import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GraduationCap, RefreshCw, X } from "lucide-react";
import { api, QaLesson, QaLessonsResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Button, Card, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { fmtDate } from "@/lib/utils";

// QA lessons (qa/lessons.py): every week the app reads QA's re-scores since the last sync,
// groups the disagreements by criterion and drafts a general rule for the ones QA corrected
// repeatedly. Nothing reaches the grading rules until an admin approves it here, and then only
// for chats graded afterwards — existing grades are never re-graded.

const ACTIVE = ["queued", "running"];
type StatusFilter = "draft" | "approved" | "rejected" | "";

function pct(x: number | null | undefined) {
  return x == null ? "—" : `${(100 * x).toFixed(1)}%`;
}

function LessonCard({ l, admin, onOpen, onChanged }: {
  l: QaLesson;
  admin: boolean;
  onOpen: (id: string) => void;
  onChanged: () => void;
}) {
  const [text, setText] = useState(l.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const draft = l.status === "draft";

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      onChanged();
    } catch (e: any) {
      setError(e.message || "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge>{l.criterion_id}</Badge>
        <Badge className={l.kind === "not_a_fail"
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600"
          : "border-destructive/40 bg-destructive/10 text-destructive"}>
          {l.kind === "not_a_fail" ? "NOT a failure" : "IS a failure"}
        </Badge>
        <Badge className="text-muted-foreground">{l.status}</Badge>
        <span className="text-muted-foreground">{fmtDate(l.created_at)}</span>
        {l.decided_by && (
          <span className="text-muted-foreground">· {l.status} by {l.decided_by}</span>
        )}
      </div>
      {draft && admin ? (
        <textarea
          className="min-h-[72px] w-full rounded-md border bg-background p-2 text-sm"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      ) : (
        <p className="text-sm">{l.text}</p>
      )}
      {l.rationale && <p className="text-xs text-muted-foreground">Why: {l.rationale}</p>}
      <div className="space-y-1">
        <div className="text-xs font-semibold uppercase text-muted-foreground">
          QA corrections behind it ({l.evidence.length})
        </div>
        <ul className="space-y-1 text-xs">
          {l.evidence.map((e) => (
            <li key={e.conversation_id} className="flex flex-wrap gap-x-2">
              <button className="font-mono text-primary hover:underline"
                      onClick={() => onOpen(e.conversation_id)}>
                {e.conversation_id}
              </button>
              <span className="text-muted-foreground">
                AI {e.ai ?? "—"} → QA {e.human}{e.inferred ? " (from the overall re-score)" : ""}
              </span>
              {e.note && <span className="italic">“{e.note}”</span>}
            </li>
          ))}
        </ul>
      </div>
      {draft && admin && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy}
                  onClick={() => act(async () => {
                    if (text.trim() !== l.text) await api.put(`/api/lessons/${l.id}`, { text });
                    await api.post(`/api/lessons/${l.id}/approve`);
                  })}>
            <Check className="mr-1 h-4 w-4" /> Approve — add to the rules
          </Button>
          <Button size="sm" variant="outline" disabled={busy}
                  onClick={() => act(() => api.post(`/api/lessons/${l.id}/reject`))}>
            <X className="mr-1 h-4 w-4" /> Reject
          </Button>
          <span className="text-xs text-muted-foreground">
            Applies to chats graded after approval. Old chats keep their grades.
          </span>
        </div>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </Card>
  );
}

export default function Lessons() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const qc = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>("draft");
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const q = useQuery({
    queryKey: ["lessons", status],
    queryFn: () => api.get<QaLessonsResponse>(`/api/lessons${status ? `?status=${status}` : ""}`),
    refetchInterval: (s) => (s.state.data?.active_job ? 3000 : false),
  });
  const running = !!q.data?.active_job && ACTIVE.includes(q.data.active_job.status);
  const [wasRunning, setWasRunning] = useState(false);
  useEffect(() => {
    if (running) setWasRunning(true);
    else if (wasRunning) {
      setWasRunning(false);
      qc.invalidateQueries({ queryKey: ["lessons"] });
    }
  }, [running]);

  const sync = async () => {
    setError("");
    try {
      await api.post("/api/lessons/sync");
      qc.invalidateQueries({ queryKey: ["lessons"] });
    } catch (e: any) {
      setError(e.message || "Failed to start the sync");
    }
  };

  const last = q.data?.last_sync;
  const s = last?.summary;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <GraduationCap className="h-6 w-6" /> QA lessons
          </h1>
          <p className="text-sm text-muted-foreground">
            Rule changes drafted from QA's re-scores, every {q.data?.every_days ?? 7} days
            {q.data && !q.data.enabled && " (weekly sync is off — QA_LESSONS=off)"}. An admin
            approves each one before it reaches the grading rules.
          </p>
        </div>
        {admin && (
          <Button onClick={sync} disabled={running}>
            {running ? <Spinner className="mr-2 h-4 w-4" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            {running ? "Syncing…" : "Sync now"}
          </Button>
        )}
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}

      {last && (
        <Card className="p-4 text-sm">
          <div className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
            Last sync · {fmtDate(last.at)} (re-scores since {fmtDate(last.since)})
          </div>
          {s && (
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              <span>{s.reviewed} reviewed ({s.re_scored} re-scored, {s.agreed} agreed)</span>
              <span>PASS/FAIL agreement on reviewed chats: <b>{pct(s.reviewed_agreement)}</b></span>
              <span>false FAIL {s.false_fail} · false PASS {s.false_pass}</span>
              <span>{last.drafted} drafted · ${last.cost_usd}</span>
            </div>
          )}
          {last.criteria && Object.keys(last.criteria).length > 0 && (
            <div className="mt-2 text-xs text-muted-foreground">
              Corrected criteria: {Object.entries(last.criteria)
                .map(([k, n]) => `${k} ${n}`).join(" · ")}
            </div>
          )}
        </Card>
      )}

      <div className="flex items-center gap-2 text-xs">
        <select className="h-8 rounded-md border bg-background px-2" value={status}
                onChange={(e) => setStatus(e.target.value as StatusFilter)}>
          <option value="draft">Drafts</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="">All</option>
        </select>
      </div>

      {q.isLoading ? (
        <Spinner />
      ) : (q.data?.lessons ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing here.</p>
      ) : (
        <div className="space-y-3">
          {q.data!.lessons.map((l) => (
            <LessonCard key={l.id} l={l} admin={admin} onOpen={setOpenId}
                        onChanged={() => qc.invalidateQueries({ queryKey: ["lessons"] })} />
          ))}
        </div>
      )}
      {openId && <ConversationDrawer id={openId} onClose={() => setOpenId(null)} readOnly />}
    </div>
  );
}
