import { useEffect, useState } from "react";
import { api, Job } from "@/lib/api";
import { Button, Input, Spinner } from "./ui/primitives";
import { X } from "lucide-react";
import AgentMultiSelect from "./AgentMultiSelect";

// Modal to trigger a fetch, review, or direct QA on specific IDs.
export default function RunDialog({
  kind,
  conversationIds,
  onClose,
  onDone,
  onPartialData,
}: {
  kind: "fetch" | "review";
  conversationIds?: string[];   // pre-selected IDs → jump straight to review
  onClose: () => void;
  onDone: () => void;
  onPartialData?: () => void;   // called as conversations arrive (fetch jobs only)
}) {
  const title = `${kind} conversations`;
  const endpoint = `/api/${kind}`;
  const [selected, setSelected] = useState<string[]>([]);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [state, setState] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");

  // When conversationIds is provided, skip the form and jump directly to review.
  const directIds = conversationIds && conversationIds.length > 0;
  useEffect(() => {
    if (directIds) startReview(conversationIds!);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Poll the active job until it reaches a terminal state.
  useEffect(() => {
    if (!job || ["done", "error"].includes(job.status)) return;
    const t = setInterval(async () => {
      const j = await api.get<Job>(`/api/jobs/${job.id}`);
      setJob(j);
      if (j.status === "done") {
        onDone();
      } else if (j.kind === "fetch" && j.result?.partial && onPartialData) {
        onPartialData();
      }
    }, 1500);
    return () => clearInterval(t);
  }, [job]);

  const start = async () => {
    setError("");
    try {
      const body =
        kind === "review"
          ? { agents: selected.length ? selected : null, since: since || null, until: until || null, state: state || null }
          : { agents: selected, since: since || null, until: until || null, state: state || null };
      const j = await api.post<Job>(endpoint, body);
      setJob(j);
    } catch (e: any) {
      setError(e.message || "Failed to start");
    }
  };

  const startReview = async (ids: string[]) => {
    setError("");
    try {
      const j = await api.post<Job>("/api/review", {
        conversation_ids: ids,
        regrade: false,
      });
      setJob(j);
    } catch (e: any) {
      setError(e.message || "Failed to start QA");
    }
  };

  const running = job && !["done", "error"].includes(job.status);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 py-10">
      <div className="w-full max-w-md rounded-lg border bg-card p-5 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold capitalize">{title}</h2>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Form — hidden when jumping straight to review from pre-selected IDs */}
        {!job && !directIds && (
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-sm font-medium">Agents</label>
              <AgentMultiSelect value={selected} onChange={setSelected} />
              {kind === "review" && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Select none to grade all fetched conversations.
                </p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-sm font-medium">Since</label>
                <Input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">Until</label>
                <Input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">State</label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                value={state}
                onChange={(e) => setState(e.target.value)}
              >
                <option value="">Any</option>
                <option value="open">Open</option>
                <option value="closed">Closed</option>
                <option value="snoozed">Snoozed</option>
              </select>
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button className="w-full" onClick={start} disabled={kind !== "review" && selected.length === 0}>
              Start {kind}
            </Button>
          </div>
        )}

        {/* Loading spinner while starting direct review */}
        {!job && directIds && (
          <div className="flex flex-col items-center gap-3 py-6">
            <Spinner className="h-6 w-6 text-primary" />
            <p className="text-sm text-muted-foreground">Starting QA for {conversationIds!.length} conversation(s)…</p>
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
        )}

        {job && (
          <div className="py-4 text-center">
            {running && <JobProgress job={job} />}

            {job.status === "done" && job.kind !== "review" && (
              <FetchDonePanel
                result={job.result}
                onRunQA={(ids) => startReview(ids)}
                onClose={onClose}
              />
            )}

            {job.status === "done" && job.kind === "review" && (
              <div>
                <p className="font-medium text-emerald-500">QA complete ✓</p>
                <pre className="mt-2 rounded bg-muted p-3 text-left text-xs">
                  {JSON.stringify(job.result, null, 2)}
                </pre>
                <Button className="mt-4 w-full" onClick={onClose}>
                  Close
                </Button>
              </div>
            )}

            {job.status === "error" && (
              <div>
                <p className="font-medium text-destructive">Failed</p>
                <p className="mt-2 text-sm text-muted-foreground">{job.error}</p>
                <Button variant="outline" className="mt-4 w-full" onClick={() => setJob(null)}>
                  Try again
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function JobProgress({ job }: { job: Job }) {
  const r = job.result;

  if (job.kind === "fetch" && r?.total > 0) {
    const pct = Math.round((r.fetched / r.total) * 100);
    return (
      <div className="flex flex-col items-center gap-3 w-full">
        <Spinner className="h-6 w-6 text-primary" />
        <div className="w-full space-y-1.5">
          <p className="text-sm text-muted-foreground">
            Fetching {r.fetched} of {r.total} conversations…
          </p>
          <ProgressBar pct={pct} />
          <p className="text-xs text-right text-muted-foreground">{pct}%</p>
        </div>
      </div>
    );
  }

  if (job.kind === "review" && r?.total > 0) {
    const done = (r.graded ?? 0) + (r.skipped ?? 0);
    const pct = Math.round((done / r.total) * 100);
    return (
      <div className="flex flex-col items-center gap-3 w-full">
        <Spinner className="h-6 w-6 text-primary" />
        <div className="w-full space-y-1.5">
          <div className="flex justify-between text-sm text-muted-foreground">
            <span>Grading {r.graded ?? 0} · skipped {r.skipped ?? 0}</span>
            <span>of {r.total}</span>
          </div>
          <ProgressBar pct={pct} />
          <p className="text-xs text-right text-muted-foreground">{pct}%</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3">
      <Spinner className="h-6 w-6 text-primary" />
      <p className="text-sm text-muted-foreground">
        {job.kind === "review" ? "Grading…" : `Job ${job.status}…`}
      </p>
    </div>
  );
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-primary transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function FetchDonePanel({
  result,
  onRunQA,
  onClose,
}: {
  result: any;
  onRunQA: (ids: string[]) => void;
  onClose: () => void;
}) {
  const ids: string[] = result?.conversation_ids ?? [];
  const count = result?.fetched ?? ids.length;
  const skipped: number = result?.skipped_deleted ?? 0;
  const saved: number = result?.saved ?? count;

  return (
    <div>
      <p className={`font-medium ${skipped ? "text-amber-500" : "text-emerald-500"}`}>
        {skipped ? "Done, with skipped conversations" : "Done ✓"}
      </p>
      <div className="mt-2 text-left text-sm space-y-2">
        <p>
          Fetched {count} conversation{count === 1 ? "" : "s"}
          {skipped > 0 && <> · stored {saved}</>}.
        </p>
        {skipped > 0 && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
            <p className="font-medium text-sm text-amber-600 dark:text-amber-400">
              {skipped} conversation{skipped === 1 ? " was" : "s were"} not imported
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {skipped === 1 ? "It is" : "They are"} in the Trash and blocked from re-import.
              Restore or purge {skipped === 1 ? "it" : "them"} from the Trash to import{" "}
              {skipped === 1 ? "it" : "them"} again.
            </p>
          </div>
        )}
        {ids.length > 0 && (
          <div className="rounded-md border border-primary/30 bg-primary/5 p-3 space-y-2">
            <p className="font-medium text-sm">Run QA now</p>
            <p className="text-xs text-muted-foreground">
              Grade these {ids.length} conversation(s) with the configured QA backend (Qwen).
            </p>
            <Button className="w-full" size="sm" onClick={() => onRunQA(ids)}>
              Run QA on these conversations
            </Button>
          </div>
        )}
      </div>
      <Button variant="outline" className="mt-4 w-full" onClick={onClose}>
        Close
      </Button>
    </div>
  );
}
