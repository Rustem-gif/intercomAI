import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Crown, Download } from "lucide-react";
import { api, VipList } from "@/lib/api";
import { Badge, Button, Card, Input, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { brandDot, brandLabel, useBrand } from "@/lib/brand";
import { cn, fmtDate } from "@/lib/utils";

// The VIP managers' email threads (Intercom team "VIP managers"), filtered down to real
// dialogues: rules first (the player said something, a manager answered), then a Claude check
// of the meaning. Not graded — a list with transcripts. Collected by `intercom-summary vip-collect`.

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value ?? "—"}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}

export default function Vip() {
  const { brands } = useBrand();
  const [agent, setAgent] = useState("");
  const [brand, setBrand] = useState("");
  const [topic, setTopic] = useState("");
  const [search, setSearch] = useState("");
  const [showExcluded, setShowExcluded] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const params = new URLSearchParams();
  if (showExcluded) params.set("include_excluded", "true");
  if (agent) params.set("agent", agent);
  if (brand) params.set("brand", brand);
  if (search.trim()) params.set("search", search.trim());

  const { data, isLoading, error } = useQuery({
    queryKey: ["vip", params.toString()],
    queryFn: () => api.get<VipList>(`/api/vip/conversations?${params.toString()}`),
  });

  const s = data?.summary;
  const topics = useMemo(
    () => [...new Set((data?.items ?? []).map((r) => r.topic).filter(Boolean))].sort(),
    [data],
  );
  const rows = (data?.items ?? []).filter((r) => !topic || r.topic === topic);
  const brandValues = useMemo(
    () => [...new Set((data?.items ?? []).map((r) => r.brand).filter(Boolean))].sort(),
    [data],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Crown className="h-5 w-5 text-amber-500" />
            <h1 className="text-2xl font-bold">VIP chats</h1>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Conversations of the Intercom “VIP managers” team
            {s?.first && ` from ${fmtDate(s.first)} to ${fmtDate(s.last)}`}. They arrive by email,
            so they are kept apart from the dashboards and are not graded. Only real dialogues are
            listed: the player wrote more than a word or a signature, a manager answered them, and
            a Claude check confirmed the thread has substance (not system mail or a “thanks”).
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => { window.location.href = "/api/vip/export.xlsx"; }}>
          <Download className="h-3.5 w-3.5" /> Export XLSX
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-16"><Spinner className="h-6 w-6 text-primary" /></div>
      ) : error ? (
        <p className="text-sm text-destructive">{String(error)}</p>
      ) : !s?.total ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">
          No VIP threads collected yet. Run <code>intercom-summary vip-collect --since … --until …</code>.
        </Card>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Real dialogues" value={s.kept} hint={`of ${s.total} threads fetched`} />
            <Stat label="Filtered out" value={s.excluded}
                  hint={`${s.excluded - s.excluded_by_ai} by rules · ${s.excluded_by_ai} by AI check`} />
            <Stat label="Started by the player"
                  value={rows.filter((r) => r.keep && r.delivered_as === "customer_initiated").length}
                  hint="among the dialogues shown" />
            <Stat label="VIP managers" value={s.managers.filter((m) => m.kept).length}
                  hint={s.managers.filter((m) => m.kept).slice(0, 3)
                    .map((m) => `${m.agent_name || "unassigned"} ${m.kept}`).join(" · ")} />
          </div>

          {s.excluded_by_rule.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span>Filtered out by rules:</span>
              {s.excluded_by_rule.map((e) => (
                <Badge key={e.reason} className="bg-muted/50">{e.reason} · {e.n}</Badge>
              ))}
              <Badge className="bg-muted/50">AI: not a real dialogue · {s.excluded_by_ai}</Badge>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Input className="h-8 w-56 text-xs" placeholder="Search player, subject, ID…"
                   value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className="h-8 rounded-md border bg-background px-2" value={agent}
                    onChange={(e) => setAgent(e.target.value)}>
              <option value="">All managers</option>
              {s.managers.map((m) => (
                <option key={m.agent_name} value={m.agent_name}>{m.agent_name || "unassigned"}</option>
              ))}
            </select>
            <select className="h-8 rounded-md border bg-background px-2" value={brand}
                    onChange={(e) => setBrand(e.target.value)}>
              <option value="">All brands</option>
              {brandValues.map((b) => <option key={b} value={b}>{brandLabel(b, brands)}</option>)}
            </select>
            <select className="h-8 rounded-md border bg-background px-2" value={topic}
                    onChange={(e) => setTopic(e.target.value)}>
              <option value="">All topics</option>
              {topics.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-muted-foreground">
              <input type="checkbox" checked={showExcluded} onChange={(e) => setShowExcluded(e.target.checked)} />
              show filtered-out
            </label>
            <span className="text-muted-foreground">{rows.length} shown</span>
          </div>

          <Card className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2.5 font-medium">Date</th>
                  <th className="px-3 py-2.5 font-medium">Player</th>
                  <th className="px-3 py-2.5 font-medium">VIP manager</th>
                  <th className="px-3 py-2.5 font-medium">Topic</th>
                  <th className="px-3 py-2.5 font-medium">Subject</th>
                  <th className="px-3 py-2.5 font-medium" title="Player messages / manager replies">Msgs</th>
                  {showExcluded && <th className="px-3 py-2.5 font-medium">Filter</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} onClick={() => setOpenId(r.id)}
                      className={cn("cursor-pointer border-b last:border-0 hover:bg-muted/50",
                        !r.keep && "opacity-60")}>
                    <td className="whitespace-nowrap px-3 py-2.5 text-xs text-muted-foreground">
                      {fmtDate(r.created_at)}
                      <div className="font-mono text-[11px]">{r.id}</div>
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-1.5">
                        {r.brand && <span className={cn("h-2 w-2 shrink-0 rounded-full", brandDot(r.brand, brands))}
                                          title={brandLabel(r.brand, brands)} />}
                        {r.customer_name || "—"}
                      </div>
                      <div className="text-xs text-muted-foreground">{r.customer_email}</div>
                    </td>
                    <td className="px-3 py-2.5">
                      {r.agent_name || "—"}
                      <div className="text-xs text-muted-foreground">
                        {r.delivered_as === "customer_initiated" ? "player wrote first" : "manager wrote first"}
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-xs">{r.topic || "—"}</td>
                    <td className="max-w-xs truncate px-3 py-2.5 text-xs text-muted-foreground" title={r.subject}>
                      {r.subject || "—"}
                    </td>
                    <td className="px-3 py-2.5 text-xs tabular-nums">{r.customer_msgs} / {r.agent_replies}</td>
                    {showExcluded && (
                      <td className="max-w-sm px-3 py-2.5 text-xs">
                        {r.keep ? (
                          <span className="text-emerald-600">kept</span>
                        ) : (
                          <span className="text-muted-foreground">
                            <span className="font-medium">{r.filter_stage === "ai" ? "AI" : "rule"}:</span> {r.reason}
                          </span>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}

      {openId && (
        <ConversationDrawer id={openId} onClose={() => setOpenId(null)} readOnly
                            detailUrl={`/api/vip/conversations/${openId}`}
                            noGradeText="VIP threads are not graded: this tab lists them for reading only." />
      )}
    </div>
  );
}
