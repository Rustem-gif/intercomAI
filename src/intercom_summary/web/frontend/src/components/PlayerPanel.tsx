import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, AlertTriangle, Info } from "lucide-react";
import { api, ChatRel, PlayerPanelData } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Badge, Button, Spinner } from "./ui/primitives";
import { cn, fmtDate } from "@/lib/utils";

// The chat's player as the casino's back office has them — the same record the grader reads
// as its BACK OFFICE block (backoffice/panel.py), so a manager can re-check a verdict without
// opening the BO. Dated rows are marked before / during / after the chat.

const REL: Record<Exclude<ChatRel, "">, { label: string; cls: string }> = {
  before: { label: "before", cls: "bg-muted text-muted-foreground" },
  during: { label: "during", cls: "bg-blue-500/15 text-blue-600 dark:text-blue-400" },
  after: { label: "after", cls: "bg-violet-500/15 text-violet-600 dark:text-violet-400" },
};

const PAY_KIND: Record<string, string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  fail: "text-red-600 dark:text-red-400",
  rejected: "text-red-600 dark:text-red-400 font-medium",
  pending: "text-amber-600 dark:text-amber-400",
};

function statusTone(key?: string) {
  if (key === "blocked" || key === "disable" || key === "suspend")
    return "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400";
  if (key?.startsWith("restrict"))
    return "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400";
  return "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
}

function When({ when, rel }: { when: string; rel: ChatRel }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
      {when ? fmtDate(when) : "—"}
      {rel && <span className={cn("rounded px-1 text-[10px] font-medium", REL[rel].cls)}>{REL[rel].label}</span>}
    </span>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {title}{count != null && count > 0 ? ` (${count})` : ""}
      </h4>
      {children}
    </div>
  );
}

const Empty = ({ text = "none" }: { text?: string }) => (
  <p className="text-xs text-muted-foreground">{text}</p>
);

export default function PlayerPanel({ url }: { url: string }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [allPayments, setAllPayments] = useState(false);
  const [allBonuses, setAllBonuses] = useState(false);
  const key = ["player", url];
  const { data, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: () => api.get<PlayerPanelData>(url),
  });
  const refresh = useMutation({
    mutationFn: () => api.post<PlayerPanelData>(`${url}/refresh`),
    onSuccess: (fresh) => qc.setQueryData(key, fresh),
  });

  if (isLoading) return <div className="flex justify-center p-6"><Spinner /></div>;
  if (error || !data) return <p className="p-4 text-sm text-red-600">Could not load the player.</p>;

  const canRefresh = canWrite(user?.role) && data.can_refresh;
  const header = (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{data.brand_label || "Back office"}</p>
        <p className="truncate font-mono text-xs">{data.player_id || "no player id"}</p>
      </div>
      <div className="flex shrink-0 gap-1">
        {canRefresh && (
          <Button size="sm" variant="outline" disabled={refresh.isPending}
                  onClick={() => refresh.mutate()} title="Re-read this player from the back office now">
            <RefreshCw className={cn("h-3.5 w-3.5", refresh.isPending && "animate-spin")} />
            Refresh
          </Button>
        )}
        {data.bo_url && (
          <a href={data.bo_url} target="_blank" rel="noreferrer"
             className="inline-flex h-8 items-center gap-1 rounded-md border px-2 text-xs font-medium hover:bg-muted">
            <ExternalLink className="h-3.5 w-3.5" /> Open in BO
          </a>
        )}
      </div>
    </div>
  );
  const refreshError = refresh.error ? (
    <p className="text-xs text-red-600">{(refresh.error as Error).message}</p>
  ) : null;

  if (!data.available) {
    return (
      <div className="space-y-3 p-4">
        {header}
        {refreshError}
        <div className="flex items-start gap-1.5 rounded-md border px-3 py-2 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {data.reason}
        </div>
      </div>
    );
  }

  const pays = (data.payments ?? []).filter((p) => allPayments || p.near);
  const bonuses = (data.bonuses ?? []).filter((b) => allBonuses || b.near);
  const spins = (data.freespins ?? []).filter((b) => allBonuses || b.near);
  const stale = (data.age_hours ?? 0) > 24;

  return (
    <div className="space-y-4 p-4 text-sm">
      {header}
      {refreshError}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge className={statusTone(data.status_key)}>{data.status}</Badge>
          {data.segment && <Badge>{data.segment}{data.loyalty_level ? ` · ${data.loyalty_level}` : ""}</Badge>}
          {data.level && <Badge className="text-muted-foreground">{data.level}</Badge>}
        </div>
        {data.status_detail && <p className="text-xs text-muted-foreground">{data.status_detail}</p>}
        {(data.tags?.length ?? 0) > 0 && (
          <div className="flex flex-wrap gap-1">
            {data.tags!.map((t) => (
              <span key={t} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">{t}</span>
            ))}
          </div>
        )}
        {(data.groups?.length ?? 0) > 0 && (
          <p className="text-[11px] text-muted-foreground">Groups: {data.groups!.join(", ")}</p>
        )}
        {data.duplicates != null && (
          <p className={cn("text-xs", data.duplicates > 0 && "font-medium text-amber-600 dark:text-amber-400")}>
            Duplicate accounts linked: {data.duplicates}
          </p>
        )}
        <p className={cn("text-[11px]", stale ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")}>
          Read from the BO {fmtDate(data.read_at)}. Status, tags and level are as of then; dated rows are history.
        </p>
      </div>

      {((data.not_read?.length ?? 0) > 0 || data.history_note) && (
        <div className="space-y-1 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs">
          {(data.not_read?.length ?? 0) > 0 && (
            <p className="flex items-start gap-1.5">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
              Not read: {data.not_read!.join(", ")} — unknown, not none.
            </p>
          )}
          {data.history_note && <p className="text-muted-foreground">{data.history_note}</p>}
        </div>
      )}

      <Section title="Locks & status changes" count={data.locks?.length}>
        {(data.locks?.length ?? 0) === 0 ? <Empty /> : data.locks!.map((l, i) => (
          <div key={i} className="rounded-md border px-2.5 py-1.5">
            <div className="flex items-start justify-between gap-2">
              <span className="text-xs font-medium">{l.kind} {l.reason}</span>
              <When when={l.when} rel={l.rel} />
            </div>
            {l.comment && <p className="text-xs text-muted-foreground">“{l.comment}”</p>}
            {(l.by || l.until) && (
              <p className="text-[11px] text-muted-foreground">
                {l.by && `by ${l.by}`}{l.by && l.until ? " · " : ""}{l.until && `until ${fmtDate(l.until)}`}
              </p>
            )}
          </div>
        ))}
      </Section>

      <Section title="RG limits" count={data.limits?.length}>
        {(data.limits?.length ?? 0) === 0 ? <Empty /> : data.limits!.map((l, i) => (
          <div key={i} className="flex items-start justify-between gap-2 text-xs">
            <span><span className="font-medium">{l.type}</span> — {l.status}{l.ends ? `, ends ${fmtDate(l.ends)}` : ""}</span>
            {l.when && <When when={l.when} rel={l.rel} />}
          </div>
        ))}
      </Section>

      <Section title="Payments" count={pays.length}>
        <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <input type="checkbox" checked={allPayments} onChange={(e) => setAllPayments(e.target.checked)} />
          all {data.payments?.length ?? 0} latest (default: 14 days before the chat to 3 after)
        </label>
        {pays.length === 0 ? <Empty /> : pays.map((p, i) => (
          <div key={i} className="border-b pb-1.5 text-xs last:border-0">
            <div className="flex items-start justify-between gap-2">
              <span><span className="font-medium">{p.action}</span> {p.amount}</span>
              <When when={p.when} rel={p.rel} />
            </div>
            <p className={PAY_KIND[p.kind]}>{p.state}</p>
            <p className="text-[11px] text-muted-foreground">
              {p.source}{p.finished ? ` · finished ${fmtDate(p.finished)}` : ""}{p.approval ? ` · ${p.approval}` : ""}
            </p>
            {p.comment && <p className="text-[11px] text-muted-foreground">{p.comment}</p>}
          </div>
        ))}
      </Section>

      {!data.unavailable?.includes("bonuses") && (
        <Section title="Bonuses & free spins" count={bonuses.length + spins.length}>
          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <input type="checkbox" checked={allBonuses} onChange={(e) => setAllBonuses(e.target.checked)} />
            all latest (default: 30 days before the chat to 3 after)
          </label>
          {bonuses.length + spins.length === 0 ? (
            <Empty text={data.not_read?.includes("bonuses") ? "not read" : "none"} />
          ) : (
            <>
              {bonuses.map((b, i) => (
                <div key={`b${i}`} className="flex items-start justify-between gap-2 text-xs">
                  <span>
                    <span className="font-medium">{b.title}</span> — {b.stage}{b.amount ? `, ${b.amount}` : ""}
                    {b.by_staff === true && (
                      <span className="ml-1 rounded bg-amber-500/15 px-1 text-[10px] font-medium text-amber-600 dark:text-amber-400">by staff</span>
                    )}
                    {b.by_staff === false && <span className="ml-1 text-[10px] text-muted-foreground">auto</span>}
                  </span>
                  <When when={b.when} rel={b.rel} />
                </div>
              ))}
              {spins.map((s, i) => (
                <div key={`s${i}`} className="flex items-start justify-between gap-2 text-xs">
                  <span><span className="font-medium">{s.title}</span> — {s.stage}</span>
                  <When when={s.when} rel={s.rel} />
                </div>
              ))}
            </>
          )}
        </Section>
      )}

      <Section title="Staff notes" count={data.comments?.length}>
        {(data.comments?.length ?? 0) === 0 ? <Empty /> : data.comments!.map((c, i) => (
          <div key={i} className="rounded-md bg-muted/50 px-2.5 py-1.5 text-xs">
            <When when={c.when} rel={c.rel} />
            <p className="mt-0.5 whitespace-pre-wrap break-words">{c.text}</p>
          </div>
        ))}
      </Section>
    </div>
  );
}
