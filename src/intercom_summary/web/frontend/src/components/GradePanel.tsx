import { useState } from "react";
import { Grade } from "@/lib/api";
import { api } from "@/lib/api";
import { Badge, Button } from "./ui/primitives";
import { Check, X, Minus, Pencil, RotateCcw } from "lucide-react";
import { scoreColor, fmtDate } from "@/lib/utils";

const verdictIcon: Record<string, React.ReactNode> = {
  pass: <Check className="h-4 w-4 text-emerald-500" />,
  fail: <X className="h-4 w-4 text-destructive" />,
  "n/a": <Minus className="h-4 w-4 text-muted-foreground" />,
};

interface Props {
  grade: Grade | null;
  conversationId?: string;
  canOverride?: boolean;
  onOverridden?: () => void;
}

export default function GradePanel({ grade, conversationId, canOverride, onOverridden }: Props) {
  const [editing, setEditing] = useState(false);
  const [scoreInput, setScoreInput] = useState(0);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

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

  const openEdit = () => {
    setScoreInput(effectiveScore);
    setReason("");
    setSaveError("");
    setEditing(true);
  };

  const submit = async () => {
    if (!reason.trim()) { setSaveError("Please explain why you are changing the score."); return; }
    if (scoreInput < 0 || scoreInput > 100) { setSaveError("Score must be 0–100."); return; }
    setSaving(true);
    setSaveError("");
    try {
      await api.post(`/api/conversations/${conversationId}/override`, {
        score: scoreInput,
        reason: reason.trim(),
      });
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
          <div className={`text-3xl font-bold ${scoreColor(effectiveScore)}`}>
            {effectiveScore}
          </div>
          <div className="mb-0.5 text-sm text-muted-foreground">/ 100</div>
          {isOverridden && (
            <div className="mb-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="rounded bg-muted px-1.5 py-0.5">
                AI: {grade.overall_score}
              </span>
              <span className={`font-medium ${delta > 0 ? "text-emerald-500" : "text-destructive"}`}>
                {delta > 0 ? "+" : ""}{delta}
              </span>
            </div>
          )}
          {canOverride && !editing && conversationId && (
            <button
              onClick={openEdit}
              className="ml-auto flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              title="Override AI score"
            >
              <Pencil className="h-3 w-3" /> Override
            </button>
          )}
        </div>

        {/* Override attribution */}
        {isOverridden && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs">
            <div className="flex items-center gap-1.5 font-medium text-amber-600 dark:text-amber-400">
              <Pencil className="h-3 w-3" />
              Overridden by {grade.overridden_by} · {fmtDate(grade.overridden_at)}
            </div>
            <p className="mt-1 text-muted-foreground italic">"{grade.override_reason}"</p>
          </div>
        )}

        {/* Override form */}
        {editing && (
          <div className="rounded-md border bg-muted/40 p-3 space-y-3">
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
            <div>
              <label className="mb-1 block text-xs font-medium">
                Reason for change <span className="text-destructive">*</span>
              </label>
              <textarea
                rows={3}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Explain why the AI score is incorrect…"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none"
              />
            </div>
            {saveError && <p className="text-xs text-destructive">{saveError}</p>}
            <div className="flex gap-2">
              <Button size="sm" onClick={submit} disabled={saving} className="flex-1">
                {saving ? "Saving…" : "Save override"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={saving}>
                <RotateCcw className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        )}
      </div>

      <p className="text-sm">{grade.summary}</p>

      {grade.violations.length > 0 && (
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
          {grade.rule_results.map((r, i) => (
            <li key={i} className="rounded-md border p-2.5">
              <div className="flex items-center gap-2">
                {verdictIcon[r.verdict] ?? verdictIcon["n/a"]}
                <span className="text-sm font-medium">{r.title || r.rule_id}</span>
                <Badge className="ml-auto border-border text-muted-foreground">{r.rule_id}</Badge>
              </div>
              {r.evidence && r.evidence.toLowerCase() !== "n/a" && (
                <p className="mt-1 border-l-2 border-border pl-2 text-xs italic text-muted-foreground">
                  "{r.evidence}"
                </p>
              )}
              {r.comment && <p className="mt-1 text-xs text-muted-foreground">{r.comment}</p>}
            </li>
          ))}
        </ul>
      </div>

      {grade.suggestions.length > 0 && (
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
