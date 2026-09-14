import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Grade, RuleResult, ManualDeduction, ManualDeductionPreset, GradeDispute, PastGrade, ScorePreview } from "@/lib/api";
import { api } from "@/lib/api";
import { Badge, Button } from "./ui/primitives";
import { Check, X, Minus, HelpCircle, Pencil, RotateCcw, SlidersHorizontal, Plus, Scale } from "lucide-react";
import { scoreColor, fmtDate } from "@/lib/utils";

/** "cannot_determine" is a real answer under QA Manual v4.1, not a missing one: the evidence a
 * criterion needs (a chat tag, a CRM escalation record, a transaction status) often lives
 * outside the transcript, and the model is required to say so rather than guess a fail. */
type Verdict = "pass" | "fail" | "n/a" | "cannot_determine";

const VERDICTS: Verdict[] = ["pass", "fail", "n/a", "cannot_determine"];

const verdictIcon: Record<string, React.ReactNode> = {
  pass: <Check className="h-4 w-4 text-emerald-500" />,
  fail: <X className="h-4 w-4 text-destructive" />,
  "n/a": <Minus className="h-4 w-4 text-muted-foreground" />,
  cannot_determine: <HelpCircle className="h-4 w-4 text-amber-500" />,
};

const verdictLabel: Record<string, string> = {
  pass: "pass", fail: "fail", "n/a": "n/a", cannot_determine: "can't tell",
};

/** What a Gate 2 Major costs is not a number of points — it caps the whole chat. Saying "−0"
 *  next to it, which is literally what the catalogue holds, would read as "free". */
function costLabel(r: RuleResult): string | null {
  if (r.critical) return null;
  if (r.severity === "major") return "caps the score";
  if (typeof r.deduction === "number" && r.deduction > 0) return `−${r.deduction}`;
  return null;
}

interface Props {
  grade: Grade | null;
  conversationId?: string;
  canOverride?: boolean;
  onOverridden?: () => void;
  /** Current grade dispute on this conversation, if any. */
  dispute?: GradeDispute | null;
  /** Scores this chat carried before a re-grade replaced them, newest first. */
  history?: PastGrade[];
  /** When set (portal context), enables the agent "Dispute this grade" action posting here. */
  disputeUrl?: string;
  readOnly?: boolean;
  onDisputeChange?: () => void;
}

export default function GradePanel({
  grade, conversationId, canOverride, onOverridden,
  dispute, history, disputeUrl, readOnly, onDisputeChange,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState<"criteria" | "manual">("criteria");
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  const [deductions, setDeductions] = useState<ManualDeduction[]>([]);
  const [scoreInput, setScoreInput] = useState(0);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  // Grade-dispute UI state. Agents raise via the portal (disputeUrl); managers
  // accept (which opens the re-score editor) or reject from the dashboard.
  const [disputeFormOpen, setDisputeFormOpen] = useState(false);
  const [disputeReason, setDisputeReason] = useState("");
  const [disputeBusy, setDisputeBusy] = useState(false);
  const [resolvingDispute, setResolvingDispute] = useState(false);

  // Catalog of manual-deduction presets (things the AI can't verify). Scoped to the ruleset
  // that produced THIS grade, which is also what the override endpoint validates against —
  // the rulesets do not offer the same presets.
  const rulesetId = grade?.ruleset_id || "default";
  const { data: dedCatalog } = useQuery({
    queryKey: ["manual-deductions", rulesetId],
    queryFn: () => api.get<{ items: ManualDeductionPreset[] }>(
      `/api/qa/manual-deductions?ruleset_id=${encodeURIComponent(rulesetId)}`),
    enabled: !!canOverride,
  });
  const presets = dedCatalog?.items ?? [];
  const presetLabel = (id: string) => presets.find((p) => p.id === id)?.label ?? id;

  // The score a save would produce, computed by the server. The panel used to mirror the
  // formula in TypeScript; with caps, a floor and a capped block that second implementation
  // would drift from the real one silently and show an analyst a number nothing would store.
  const liveDeductions = deductions.filter((d) => Number(d.points) > 0);
  const { data: preview } = useQuery({
    queryKey: ["score-preview", rulesetId, JSON.stringify(verdicts), JSON.stringify(liveDeductions)],
    queryFn: () => api.post<ScorePreview>("/api/qa/preview-score", {
      ruleset_id: rulesetId,
      criteria: verdicts,
      manual_deductions: liveDeductions,
    }),
    enabled: editing && mode === "criteria" && Object.keys(verdicts).length > 0,
    placeholderData: keepPreviousData,
  });

  if (!grade) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
        Not graded yet. Use "Run QA" to grade this conversation.
      </div>
    );
  }

  const effectiveScore = grade.human_score ?? grade.overall_score;
  // While editing, the verdict shown is the one a save would produce, not the stored one.
  const result = editing && mode === "criteria"
    ? (preview?.result ?? grade.overall_result)
    : grade.overall_result;
  const isOverridden = grade.human_score !== null && grade.human_score !== undefined;
  const delta = isOverridden ? grade.human_score! - grade.overall_score : 0;

  // Criterion re-scoring is only possible when every rule carries a known deduction
  // (i.e. an Ollama casino grade). Legacy/Claude grades fall back to the manual slider.
  const criteriaAvailable =
    grade.rule_results.length > 0 &&
    grade.rule_results.every((r) => typeof r.deduction === "number");

  // Verdict actually in force now (analyst change layered over the AI verdict).
  const liveVerdict = (r: RuleResult): Verdict =>
    (grade.human_criteria?.[r.rule_id] as Verdict) ?? (r.verdict as Verdict);

  const openEdit = () => {
    const init: Record<string, Verdict> = {};
    for (const r of grade.rule_results) init[r.rule_id] = liveVerdict(r);
    setVerdicts(init);
    setDeductions(grade.human_deductions ? grade.human_deductions.map((d) => ({ ...d })) : []);
    setScoreInput(effectiveScore);
    setMode(criteriaAvailable ? "criteria" : "manual");
    setReason("");
    setSaveError("");
    setEditing(true);
  };

  const previewScore =
    mode === "criteria" ? (preview?.score ?? effectiveScore) : scoreInput;
  const previewDelta = previewScore - grade.overall_score;

  const addDeduction = () =>
    setDeductions((prev) => [
      ...prev,
      { category: presets[0]?.id ?? "info-correctness", points: 5, note: "" },
    ]);
  const updateDeduction = (i: number, patch: Partial<ManualDeduction>) =>
    setDeductions((prev) => prev.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  const removeDeduction = (i: number) =>
    setDeductions((prev) => prev.filter((_, j) => j !== i));

  const submit = async () => {
    if (!reason.trim()) { setSaveError("Please explain why you are changing the score."); return; }
    setSaving(true);
    setSaveError("");
    try {
      if (mode === "criteria") {
        const cleaned = deductions.filter((d) => Number(d.points) > 0);
        // Send the full verdict map + manual deductions; the server computes the diff and
        // the authoritative score (criteria deductions + manual deductions).
        await api.post(`/api/conversations/${conversationId}/override`, {
          criteria: verdicts,
          manual_deductions: cleaned,
          reason: reason.trim(),
        });
      } else {
        if (scoreInput < 0 || scoreInput > 100) { setSaveError("Score must be 0–100."); return; }
        await api.post(`/api/conversations/${conversationId}/override`, {
          score: scoreInput,
          reason: reason.trim(),
        });
      }
      // If this re-score is resolving an agent's dispute, mark the dispute accepted now
      // that the corrected score has been applied.
      if (resolvingDispute) {
        await api.post(`/api/conversations/${conversationId}/grade-dispute/resolve`, {
          status: "accepted",
          note: reason.trim(),
        });
        setResolvingDispute(false);
        onDisputeChange?.();
      }
      setEditing(false);
      onOverridden?.();
    } catch (e: any) {
      setSaveError(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const submitDispute = async () => {
    if (!disputeReason.trim()) return;
    setDisputeBusy(true);
    try {
      await api.post(disputeUrl ?? `/api/conversations/${conversationId}/grade-dispute`, {
        reason: disputeReason.trim(),
      });
      setDisputeFormOpen(false);
      setDisputeReason("");
      onDisputeChange?.();
    } finally {
      setDisputeBusy(false);
    }
  };

  const acceptDispute = () => {
    // Accepting opens the re-score editor; the score change is saved through the
    // existing override flow, and submit() then marks the dispute accepted.
    setResolvingDispute(true);
    openEdit();
  };

  const rejectDispute = async () => {
    setDisputeBusy(true);
    try {
      await api.post(`/api/conversations/${conversationId}/grade-dispute/resolve`, {
        status: "rejected",
      });
      onDisputeChange?.();
    } finally {
      setDisputeBusy(false);
    }
  };

  // Agent (portal) may dispute; analyst (dashboard) raises via the editor area too.
  const canRaiseDispute =
    !!conversationId && (readOnly ? !!disputeUrl : !!canOverride) &&
    (!dispute || dispute.status === "rejected");
  const canResolveDispute = !!canOverride && dispute?.status === "open";

  return (
    <div className="space-y-5 p-5">
      {/* Score header */}
      <div className="space-y-2">
        <div className="flex items-end gap-3">
          <div className={`text-3xl font-bold ${scoreColor(editing ? previewScore : effectiveScore)}`}>
            {editing ? previewScore : effectiveScore}
          </div>
          <div className="mb-0.5 text-sm text-muted-foreground">/ 100</div>
          {(editing ? previewDelta !== 0 : isOverridden) && (
            <div className="mb-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="rounded bg-muted px-1.5 py-0.5">AI: {grade.overall_score}</span>
              <span className={`font-medium ${(editing ? previewDelta : delta) > 0 ? "text-emerald-500" : "text-destructive"}`}>
                {(editing ? previewDelta : delta) > 0 ? "+" : ""}{editing ? previewDelta : delta}
              </span>
            </div>
          )}
          {canOverride && !editing && conversationId && (
            <button
              onClick={openEdit}
              className="ml-auto flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              title="Re-score this conversation"
            >
              <Pencil className="h-3 w-3" /> Re-score
            </button>
          )}
        </div>

        {/* Verdict and case state. The score says how well the agent worked; outcome status
            says what actually became of the player's issue, and the two legitimately
            disagree — a flawless chat can sit at Pending-legitimate while Finance works. */}
        {(result || grade.outcome_status || grade.severity) && (
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            {result && (
              <span className={`rounded px-1.5 py-0.5 font-semibold ${
                result === "PASS"
                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                  : "bg-destructive/10 text-destructive"
              }`}>{result}</span>
            )}
            {grade.outcome_status && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground"
                    title="What became of the player's issue — reported separately from the score">
                {grade.outcome_status}
              </span>
            )}
            {grade.severity && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
                {grade.severity}
              </span>
            )}
            {grade.case_type && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
                {grade.case_type}
              </span>
            )}
            {grade.risk_flag && grade.risk_flag !== "None" && (
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-medium text-amber-600 dark:text-amber-400">
                risk: {grade.risk_flag}
              </span>
            )}
            {grade.confidence && grade.confidence !== "High" && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground"
                    title="The grader's own certainty about this evaluation">
                {grade.confidence} confidence
              </span>
            )}
          </div>
        )}

        {/* Catastrophic service failure is NOT a compliance breach, and the difference is the
            reason it has its own score and its own flag. Labelling it "critical" here would
            undo that. */}
        {grade.catastrophic_service_failure && !editing && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <span className="font-medium">Catastrophic service failure</span> — the outcome and
            every process check failed at once. This is a service collapse, not a compliance or
            RG breach.
          </div>
        )}

        {grade.manual_review_needed && !editing && (
          <div className="flex items-start gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs">
            <HelpCircle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" />
            <span className="text-muted-foreground">
              <span className="font-medium text-amber-600 dark:text-amber-400">Needs a QC check</span>
              {grade.manual_review_reason ? ` — ${grade.manual_review_reason}` : ""}
            </span>
          </div>
        )}

        {/* Scores this chat used to carry. A grade is re-run whenever the rulebook changes, so
            "it was green last week" is a question people genuinely ask — in September it was
            asked about a whole month of chats and had no answer at all. */}
        {!editing && history && history.length > 0 && (
          <details className="rounded-md border px-3 py-2 text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              Re-graded {history.length === 1 ? "once" : `${history.length} times`} — see
              previous {history.length === 1 ? "score" : "scores"}
            </summary>
            <ul className="mt-2 space-y-1">
              {history.map((h, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 text-muted-foreground">
                  <span className={`font-medium ${scoreColor(h.human_score ?? h.overall_score)}`}>
                    {h.human_score ?? h.overall_score}
                  </span>
                  <span>until {fmtDate(h.archived_at)}</span>
                  {h.rules_version && (
                    <code className="rounded bg-muted px-1 text-[10px]">{h.rules_version}</code>
                  )}
                  {h.human_score != null && (
                    <span className="text-[10px] italic">analyst score</span>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}

        {/* Multi-intent coverage: what the player actually asked for, one row each. */}
        {!editing && grade.requests && grade.requests.length > 0 && (
          <div className="rounded-md border px-3 py-2">
            <h4 className="mb-1 text-[10px] font-semibold uppercase text-muted-foreground">
              What the player asked
            </h4>
            <ul className="space-y-1">
              {grade.requests.map((rq, i) => (
                <li key={i} className="flex items-start gap-1.5 text-xs">
                  <span className={`mt-0.5 shrink-0 rounded px-1 text-[10px] ${
                    rq.status === "unresolved"
                      ? "bg-destructive/10 text-destructive"
                      : "bg-muted text-muted-foreground"
                  }`}>{rq.status}</span>
                  <span className="text-muted-foreground">{rq.text}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Override attribution */}
        {isOverridden && !editing && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs">
            <div className="flex items-center gap-1.5 font-medium text-amber-600 dark:text-amber-400">
              <Pencil className="h-3 w-3" />
              Overridden by {grade.overridden_by} · {fmtDate(grade.overridden_at)}
              {grade.human_criteria && Object.keys(grade.human_criteria).length > 0 && (
                <span className="font-normal text-muted-foreground">
                  · {Object.keys(grade.human_criteria).length} criteria changed
                </span>
              )}
            </div>
            <p className="mt-1 text-muted-foreground italic">"{grade.override_reason}"</p>
            {grade.human_deductions && grade.human_deductions.length > 0 && (
              <ul className="mt-1.5 space-y-0.5">
                {grade.human_deductions.map((d, i) => (
                  <li key={i} className="flex items-center gap-1 text-[11px] text-muted-foreground">
                    <span className="font-medium text-destructive">−{d.points}</span>
                    <span>{presetLabel(d.category)}</span>
                    {d.note && <span className="italic">· {d.note}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Grade dispute */}
        {!editing && (
          <div className="space-y-2">
            {dispute?.status === "open" && (
              <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs">
                <div className="flex items-center gap-1.5 font-medium text-amber-600 dark:text-amber-400">
                  <Scale className="h-3 w-3" /> Grade disputed by {dispute.created_by}
                </div>
                <p className="mt-1 italic text-muted-foreground">"{dispute.reason}"</p>
                {canResolveDispute && (
                  <div className="mt-2 flex gap-2">
                    <Button size="sm" onClick={acceptDispute} disabled={disputeBusy}>
                      Accept &amp; re-score
                    </Button>
                    <Button size="sm" variant="outline" onClick={rejectDispute} disabled={disputeBusy}>
                      Reject
                    </Button>
                  </div>
                )}
              </div>
            )}
            {dispute?.status === "accepted" && (
              <div className="text-xs text-muted-foreground">
                Grade dispute accepted{dispute.resolved_by ? ` by ${dispute.resolved_by}` : ""} — score was revised.
              </div>
            )}
            {dispute?.status === "rejected" && (
              <div className="text-xs text-muted-foreground">
                Grade dispute rejected{dispute.resolved_by ? ` by ${dispute.resolved_by}` : ""} — score stands.
              </div>
            )}

            {canRaiseDispute && !disputeFormOpen && (
              <button
                onClick={() => setDisputeFormOpen(true)}
                className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
              >
                <Scale className="h-3 w-3" /> Dispute this grade
              </button>
            )}
            {canRaiseDispute && disputeFormOpen && (
              <div className="space-y-1.5">
                <textarea
                  rows={2}
                  value={disputeReason}
                  onChange={(e) => setDisputeReason(e.target.value)}
                  placeholder="Why do you disagree with this grade?"
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none"
                />
                <div className="flex gap-2">
                  <Button size="sm" onClick={submitDispute} disabled={!disputeReason.trim() || disputeBusy}>
                    {disputeBusy ? "Submitting…" : "Submit dispute"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setDisputeFormOpen(false)} disabled={disputeBusy}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Edit toolbar */}
        {editing && (
          <div className="rounded-md border bg-muted/40 p-3 space-y-3">
            {criteriaAvailable && (
              <div className="flex items-center gap-2 text-xs">
                <button
                  onClick={() => setMode("criteria")}
                  className={`rounded-md px-2 py-1 ${mode === "criteria" ? "bg-primary text-primary-foreground" : "border text-muted-foreground hover:bg-muted"}`}
                >
                  By criteria (auto)
                </button>
                <button
                  onClick={() => setMode("manual")}
                  className={`flex items-center gap-1 rounded-md px-2 py-1 ${mode === "manual" ? "bg-primary text-primary-foreground" : "border text-muted-foreground hover:bg-muted"}`}
                >
                  <SlidersHorizontal className="h-3 w-3" /> Manual
                </button>
              </div>
            )}

            {mode === "criteria" ? (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  Toggle a criterion's verdict below — the score recalculates from the ruleset weights.
                </p>
                {/* Manual deductions: things the AI can't verify (e.g. information correctness). */}
                <div className="rounded-md border border-dashed p-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium">Manual deductions <span className="text-muted-foreground">(AI can't verify)</span></span>
                    <button
                      onClick={addDeduction}
                      className="flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
                    >
                      <Plus className="h-3 w-3" /> Add
                    </button>
                  </div>
                  {deductions.length === 0 ? (
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      e.g. wrong information given, incorrect bonus applied.
                    </p>
                  ) : (
                    <div className="mt-2 space-y-2">
                      {deductions.map((d, i) => (
                        <div key={i} className="flex flex-wrap items-center gap-1.5">
                          <select
                            value={d.category}
                            onChange={(e) => updateDeduction(i, { category: e.target.value })}
                            className="rounded border bg-background px-1.5 py-1 text-xs"
                          >
                            {presets.map((p) => (
                              <option key={p.id} value={p.id}>{p.label}</option>
                            ))}
                          </select>
                          <span className="text-xs text-muted-foreground">−</span>
                          <input
                            type="number"
                            min={1}
                            max={100}
                            value={d.points}
                            onChange={(e) => updateDeduction(i, { points: Number(e.target.value) })}
                            className="w-14 rounded border bg-background px-1.5 py-1 text-xs"
                          />
                          <input
                            type="text"
                            value={d.note}
                            placeholder="note (optional)"
                            onChange={(e) => updateDeduction(i, { note: e.target.value })}
                            className="min-w-0 flex-1 rounded border bg-background px-1.5 py-1 text-xs"
                          />
                          <button onClick={() => removeDeduction(i)} className="rounded p-1 text-muted-foreground hover:bg-muted">
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs font-medium">
                  <span>New score</span>
                  <span className={`text-base font-bold ${scoreColor(scoreInput)}`}>{scoreInput}</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={scoreInput}
                  onChange={(e) => setScoreInput(Number(e.target.value))}
                  className="w-full accent-primary"
                />
                <div className="flex justify-between text-[10px] text-muted-foreground">
                  <span>0</span><span>50</span><span>100</span>
                </div>
              </div>
            )}

            <div>
              <label className="mb-1 block text-xs font-medium">
                Reason for change <span className="text-destructive">*</span>
              </label>
              <textarea
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Explain why the AI grade is incorrect…"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none"
              />
            </div>
            {saveError && <p className="text-xs text-destructive">{saveError}</p>}
            <div className="flex gap-2">
              <Button size="sm" onClick={submit} disabled={saving} className="flex-1">
                {saving ? "Saving…" : "Save re-score"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={saving}>
                <RotateCcw className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        )}
      </div>

      <p className="text-sm">{grade.summary}</p>

      {grade.violations.length > 0 && !editing && (
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Violations</h4>
          <ul className="space-y-1">
            {grade.violations.map((v, i) => (
              <li key={i} className="text-sm text-destructive">• {v}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Rule checks</h4>
        <ul className="space-y-2">
          {grade.rule_results.map((r, i) => {
            const current = editing && mode === "criteria"
              ? verdicts[r.rule_id]
              : liveVerdict(r);
            const changed = !editing && grade.human_criteria?.[r.rule_id] != null
              && grade.human_criteria[r.rule_id] !== r.verdict;
            return (
              <li key={i} className="rounded-md border p-2.5">
                <div className="flex items-center gap-2">
                  {verdictIcon[current] ?? verdictIcon["n/a"]}
                  <span className="text-sm font-medium">{r.title || r.rule_id}</span>
                  {costLabel(r) && (
                    <span className="text-[10px] text-muted-foreground">{costLabel(r)}</span>
                  )}
                  {r.group && (
                    <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground"
                          title={`Capped as a block with the rest of ${r.group}`}>{r.group}</span>
                  )}
                  {r.gate && (
                    <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground"
                          title="Which gate of the v4.1 model this sits in">gate {r.gate}</span>
                  )}
                  {r.critical && (
                    <span className="rounded bg-destructive/10 px-1 text-[10px] font-medium text-destructive">critical</span>
                  )}
                  {r.severity === "major" && !r.critical && (
                    <span className="rounded bg-orange-500/15 px-1 text-[10px] font-medium text-orange-600 dark:text-orange-400">major</span>
                  )}
                  {changed && (
                    <span className="rounded bg-amber-500/15 px-1 text-[10px] font-medium text-amber-600 dark:text-amber-400">changed</span>
                  )}
                  <Badge className="ml-auto border-border text-muted-foreground">{r.rule_id}</Badge>
                </div>

                {r.evidence && r.evidence.toLowerCase() !== "n/a" && (
                  <p className="mt-1 border-l-2 border-border pl-2 text-xs italic text-muted-foreground">
                    "{r.evidence}"
                  </p>
                )}
                {r.comment && <p className="mt-1 text-xs text-muted-foreground">{r.comment}</p>}

                {editing && mode === "criteria" && (
                  <div className="mt-2 flex gap-1">
                    {VERDICTS.map((v) => (
                      <button
                        key={v}
                        onClick={() => setVerdicts((prev) => ({ ...prev, [r.rule_id]: v }))}
                        title={v === "cannot_determine"
                          ? "The evidence this criterion needs is not in the available data"
                          : undefined}
                        className={`flex items-center gap-1 rounded-md border px-2 py-1 text-xs ${
                          verdicts[r.rule_id] === v
                            ? v === "fail"
                              ? "border-destructive bg-destructive/10 text-destructive"
                              : v === "pass"
                                ? "border-emerald-500 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                                : v === "cannot_determine"
                                  ? "border-amber-500 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                                  : "border-border bg-muted"
                            : "text-muted-foreground hover:bg-muted"
                        }`}
                      >
                        {verdictIcon[v]} {verdictLabel[v]}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      {grade.suggestions.length > 0 && !editing && (
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase text-muted-foreground">Suggestions</h4>
          <ul className="space-y-1">
            {grade.suggestions.map((s, i) => (
              <li key={i} className="text-sm text-muted-foreground">• {s}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
