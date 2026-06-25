import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Grade, RuleResult, ManualDeduction, ManualDeductionPreset } from "@/lib/api";
import { api } from "@/lib/api";
import { Badge, Button } from "./ui/primitives";
import { Check, X, Minus, Pencil, RotateCcw, SlidersHorizontal, Plus } from "lucide-react";
import { scoreColor, fmtDate } from "@/lib/utils";

type Verdict = "pass" | "fail" | "n/a";

const verdictIcon: Record<string, React.ReactNode> = {
  pass: <Check className="h-4 w-4 text-emerald-500" />,
  fail: <X className="h-4 w-4 text-destructive" />,
  "n/a": <Minus className="h-4 w-4 text-muted-foreground" />,
};

/** Recompute the score from criterion verdicts, mirroring the server formula
 * (qa/schema.py:score_from_verdicts): a fail on a critical criterion forces 0,
 * otherwise 100 minus the deductions of all failed criteria, floored at 0. */
function computeScore(rules: RuleResult[], verdicts: Record<string, Verdict>): number {
  let total = 0;
  for (const r of rules) {
    if (verdicts[r.rule_id] !== "fail") continue;
    if (r.critical) return 0;
    total += r.deduction ?? 0;
  }
  return Math.max(0, 100 - total);
}

interface Props {
  grade: Grade | null;
  conversationId?: string;
  canOverride?: boolean;
  onOverridden?: () => void;
}

export default function GradePanel({ grade, conversationId, canOverride, onOverridden }: Props) {
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState<"criteria" | "manual">("criteria");
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  const [deductions, setDeductions] = useState<ManualDeduction[]>([]);
  const [scoreInput, setScoreInput] = useState(0);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  // Catalog of manual-deduction presets (things the AI can't verify). Only needed while editing.
  const { data: dedCatalog } = useQuery({
    queryKey: ["manual-deductions"],
    queryFn: () => api.get<{ items: ManualDeductionPreset[] }>("/api/qa/manual-deductions"),
    enabled: !!canOverride,
  });
  const presets = dedCatalog?.items ?? [];
  const presetLabel = (id: string) => presets.find((p) => p.id === id)?.label ?? id;

  if (!grade) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
        Not graded yet. Use "Run QA" to grade this conversation.
      </div>
    );
  }

  const effectiveScore = grade.human_score ?? grade.overall_score;
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

  const manualTotal = deductions.reduce((s, d) => s + (Number(d.points) || 0), 0);
  const previewScore =
    mode === "criteria"
      ? Math.max(0, computeScore(grade.rule_results, verdicts) - manualTotal)
      : scoreInput;
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
      setEditing(false);
      onOverridden?.();
    } catch (e: any) {
      setSaveError(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

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
                  {typeof r.deduction === "number" && r.deduction > 0 && (
                    <span className="text-[10px] text-muted-foreground">−{r.deduction}</span>
                  )}
                  {r.critical && (
                    <span className="rounded bg-destructive/10 px-1 text-[10px] font-medium text-destructive">critical</span>
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
                    {(["pass", "fail", "n/a"] as Verdict[]).map((v) => (
                      <button
                        key={v}
                        onClick={() => setVerdicts((prev) => ({ ...prev, [r.rule_id]: v }))}
                        className={`flex items-center gap-1 rounded-md border px-2 py-1 text-xs capitalize ${
                          verdicts[r.rule_id] === v
                            ? v === "fail"
                              ? "border-destructive bg-destructive/10 text-destructive"
                              : v === "pass"
                                ? "border-emerald-500 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                                : "border-border bg-muted"
                            : "text-muted-foreground hover:bg-muted"
                        }`}
                      >
                        {verdictIcon[v]} {v}
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
