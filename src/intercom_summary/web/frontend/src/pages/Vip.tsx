import { useMemo, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Crown, Download, Gauge } from "lucide-react";
import { api, Job, VipCaseList, VipExceptions, VipList } from "@/lib/api";
import { Badge, Button, Card, Input, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import { useAuth } from "@/lib/auth";
import { brandDot, brandLabel, useBrand } from "@/lib/brand";
import { cn, fmtDate, scoreColor } from "@/lib/utils";

// The VIP managers' email threads (Intercom team "VIP managers"), filtered down to real
// dialogues: rules first (the player said something, a manager answered), then a Claude check
// of the meaning. Collected by `intercom-summary vip-collect`.
//
// Three views: the threads; the cases they group into (one player's topic across threads and
// days — the unit the VIP Scorecard v1.6 grades, see vip_cases.py); and the Exception Report
// (unanswered player messages, threads with no owner), which needs no grading at all.

type View = "threads" | "cases" | "exceptions";

// Rough per-case price of a vip-v16 grade (Sonnet, long cached prompt + KB, ~5k-char case).
const CASE_COST_LIVE = 0.05;

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
  const [view, setView] = useState<View>("threads");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Crown className="h-5 w-5 text-amber-500" />
            <h1 className="text-2xl font-bold">VIP chats</h1>
          </div>
        </div>
        <div className="flex rounded-md border p-0.5 text-xs">
          {(["threads", "cases", "exceptions"] as View[]).map((v) => (
            <button key={v} onClick={() => setView(v)}
                    className={cn("rounded px-3 py-1 capitalize",
                      view === v ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>
              {v}
            </button>
          ))}
        </div>
      </div>
      {view === "threads" ? <ThreadsView /> : view === "cases" ? <CasesView /> : <ExceptionsView />}
    </div>
  );
}

function ThreadsView() {
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
    // Keep the table (and the focused search box) on screen while the next filter loads.
    placeholderData: keepPreviousData,
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
        <p className="max-w-3xl text-sm text-muted-foreground">
          Conversations of the Intercom “VIP managers” team
          {s?.first && ` from ${fmtDate(s.first)} to ${fmtDate(s.last)}`}. They arrive by email,
          so they are kept apart from the dashboards. Only real dialogues are listed: the player
          wrote more than a word or a signature, a manager answered them, and a Claude check
          confirmed the thread has substance (not system mail or a “thanks”). Grades are given
          per case — see Cases.
        </p>
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
                            noGradeText="Threads are not graded one by one — open the Cases view: a VIP case groups this player's threads on the topic and is graded on the VIP Scorecard v1.6." />
      )}
    </div>
  );
}

function CasesView() {
  const { brands } = useBrand();
  const { user } = useAuth();
  const qc = useQueryClient();
  const [manager, setManager] = useState("");
  const [search, setSearch] = useState("");
  const [result, setResult] = useState("");
  const [flag, setFlag] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [startError, setStartError] = useState("");

  const params = new URLSearchParams();
  if (manager) params.set("manager", manager);
  if (search.trim()) params.set("search", search.trim());
  if (result) params.set("result", result);
  if (flag) params.set("flag", flag);

  const { data, isLoading, error } = useQuery({
    queryKey: ["vip-cases", params.toString()],
    queryFn: () => api.get<VipCaseList>(`/api/vip/cases?${params.toString()}`),
    placeholderData: keepPreviousData,
  });
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) => {
      const st = q.state.data?.status;
      if (st && !["queued", "running", "cancelling"].includes(st)) {
        qc.invalidateQueries({ queryKey: ["vip-cases"] });
        return false;
      }
      return 3000;
    },
  });
  const running = !!job.data && ["queued", "running", "cancelling"].includes(job.data.status);

  const s = data?.summary;
  const items = data?.items ?? [];
  const managers = useMemo(
    () => [...new Set(items.map((c) => c.primary_manager).filter(Boolean))].sort(),
    [items],
  );

  const grade = async () => {
    setStartError("");
    try {
      // Batch: half price; results arrive in minutes to hours.
      const j = await api.post<Job>("/api/vip/review", { batch: true });
      setJobId(j.id);
    } catch (e: any) {
      setStartError(e.message || "Could not start grading");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted-foreground">
        A case is one player's topic followed across threads and days: same player, same topic,
        each thread starting within 7 days of the case's last message. That is the unit the
        VIP Scorecard v1.6 grades. Criteria whose evidence lives in Slack, the back office, Jira
        or the VIP tables are marked “not in Intercom” and cost nothing.
      </p>
      {s && !s.shifts_known && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-muted-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
          No shift schedule configured (config/vip_staff.yaml), so the first- and next-response
          SLAs are not assessed. Reply gaps are still shown in each case.
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-16"><Spinner className="h-6 w-6 text-primary" /></div>
      ) : error ? (
        <p className="text-sm text-destructive">{String(error)}</p>
      ) : !s?.cases ? (
        <Card className="p-8 text-center text-sm text-muted-foreground">
          No VIP cases yet — collect threads with <code>intercom-summary vip-collect</code>.
        </Card>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Cases" value={s.cases}
                  hint={`${s.threads} threads · ${s.multi_thread} cases span several`} />
            <Stat label="Graded" value={s.graded}
                  hint={s.to_grade ? `${s.to_grade} to grade${s.stale ? ` (${s.stale} stale)` : ""}` : "all current"} />
            <Stat label="PASS" value={s.graded ? `${Math.round((100 * s.passed) / s.graded)}%` : "—"}
                  hint={s.avg_score != null ? `average ${s.avg_score}` : "no grades yet"} />
            <Stat label="Flags" value={s.critical + s.service_failure}
                  hint={`${s.critical} critical · ${s.service_failure} service failure · ${s.manual_review} to review`} />
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Input className="h-8 w-56 text-xs" placeholder="Search player, topic, case or thread ID…"
                   value={search} onChange={(e) => setSearch(e.target.value)} />
            <select className="h-8 rounded-md border bg-background px-2" value={manager}
                    onChange={(e) => setManager(e.target.value)}>
              <option value="">All VIP managers</option>
              {managers.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <select className="h-8 rounded-md border bg-background px-2" value={result}
                    onChange={(e) => setResult(e.target.value)}>
              <option value="">Any result</option>
              <option value="PASS">PASS</option>
              <option value="FAIL">FAIL</option>
              <option value="ungraded">Not graded</option>
              <option value="stale">Grade out of date</option>
            </select>
            <select className="h-8 rounded-md border bg-background px-2" value={flag}
                    onChange={(e) => setFlag(e.target.value)}>
              <option value="">Any flag</option>
              <option value="critical">Critical</option>
              <option value="service_failure">Service failure</option>
              <option value="manual">Needs review</option>
            </select>
            <span className="text-muted-foreground">{items.length} shown</span>
            {user?.role === "admin" && s.to_grade > 0 && (
              <div className="ml-auto flex items-center gap-2">
                {running ? (
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <Spinner className="h-3.5 w-3.5" /> Grading…{" "}
                    {job.data?.result?.total ? `${(job.data.result.graded ?? 0) + (job.data.result.skipped ?? 0)} / ${job.data.result.total}` : ""}
                  </span>
                ) : confirming ? (
                  <>
                    <span className="text-muted-foreground">
                      Grade {s.to_grade} case{s.to_grade === 1 ? "" : "s"} by batch, ≈ $
                      {(s.to_grade * CASE_COST_LIVE * 0.5).toFixed(2)}?
                    </span>
                    <Button size="sm" onClick={grade}>Start</Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
                  </>
                ) : (
                  <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
                    <Gauge className="h-3.5 w-3.5" /> Grade cases
                  </Button>
                )}
              </div>
            )}
          </div>
          {(startError || job.data?.status === "error") && (
            <p className="text-xs text-destructive">{startError || job.data?.error}</p>
          )}

          <Card className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2.5 font-medium">Period</th>
                  <th className="px-3 py-2.5 font-medium">Player</th>
                  <th className="px-3 py-2.5 font-medium">Topic</th>
                  <th className="px-3 py-2.5 font-medium">VIP managers</th>
                  <th className="px-3 py-2.5 font-medium">Threads</th>
                  <th className="px-3 py-2.5 font-medium">Score</th>
                  <th className="px-3 py-2.5 font-medium">Flags</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => {
                  const g = c.grade;
                  const score = g ? g.human_score ?? g.overall_score : null;
                  return (
                    <tr key={c.case_id} onClick={() => setOpenId(c.case_id)}
                        className="cursor-pointer border-b last:border-0 hover:bg-muted/50">
                      <td className="whitespace-nowrap px-3 py-2.5 text-xs text-muted-foreground">
                        {fmtDate(c.first_at)}
                        {c.last_at?.slice(0, 10) !== c.first_at?.slice(0, 10) && <> – {fmtDate(c.last_at)}</>}
                        <div className="font-mono text-[11px]">{c.case_id}</div>
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-1.5">
                          {c.brand && <span className={cn("h-2 w-2 shrink-0 rounded-full", brandDot(c.brand, brands))}
                                            title={brandLabel(c.brand, brands)} />}
                          {c.contact_name || "—"}
                        </div>
                        <div className="text-xs text-muted-foreground">{c.contact_email}</div>
                      </td>
                      <td className="px-3 py-2.5 text-xs">{c.topic || "—"}</td>
                      <td className="px-3 py-2.5 text-xs">
                        <span className="font-medium">{c.primary_manager || "—"}</span>
                        {c.managers.length > 1 && (
                          <div className="text-muted-foreground"
                               title={c.managers.filter((m) => m !== c.primary_manager).join(", ")}>
                            + {c.managers.length - 1} other{c.managers.length === 2 ? "" : "s"}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-xs tabular-nums">{c.thread_count}</td>
                      <td className="px-3 py-2.5 text-xs">
                        {g ? (
                          <span className="flex items-center gap-1.5">
                            <span className={cn("text-base font-semibold tabular-nums", scoreColor(score))}>{score}</span>
                            <span className={cn("rounded px-1 text-[10px] font-semibold",
                              (g.human_score != null ? g.human_score >= 85 && !g.critical_flag : g.overall_result === "PASS")
                                ? "bg-emerald-500/15 text-emerald-600" : "bg-destructive/10 text-destructive")}>
                              {g.human_score != null
                                ? (g.human_score >= 85 && !g.critical_flag ? "PASS" : "FAIL")
                                : g.overall_result}
                            </span>
                            {g.stale && <span className="text-[10px] text-muted-foreground" title="The case grew since it was graded">stale</span>}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">not graded</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="flex flex-wrap gap-1">
                          {g?.critical_flag && <Badge className="border-destructive/40 bg-destructive/10 text-destructive">critical</Badge>}
                          {g?.service_failure_flag && <Badge className="border-orange-500/40 bg-orange-500/10 text-orange-600">service failure</Badge>}
                          {g?.manual_review_needed && <Badge className="bg-amber-500/10 text-amber-600">review</Badge>}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        </>
      )}

      {openId && (
        <ConversationDrawer id={openId} onClose={() => setOpenId(null)} title={`Case ${openId}`}
                            detailUrl={`/api/vip/cases/${openId}`}
                            overrideUrl={`/api/vip/grades/${openId}/override`}
                            onOverridden={() => qc.invalidateQueries({ queryKey: ["vip-cases"] })}
                            transcriptText
                            noGradeText="This case has not been graded yet. Grading runs on the VIP Scorecard v1.6 (paid: Claude API)." />
      )}
    </div>
  );
}

function ExceptionsView() {
  const [openId, setOpenId] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["vip-exceptions"],
    queryFn: () => api.get<VipExceptions>("/api/vip/exceptions"),
  });
  if (isLoading) return <div className="flex justify-center py-16"><Spinner className="h-6 w-6 text-primary" /></div>;
  if (error || !data) return <p className="text-sm text-destructive">{String(error)}</p>;
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-muted-foreground">
        Events a reviewer should see without waiting for a grade (scorecard Appendix В), as of the
        last collection{data.as_of ? ` (${fmtDate(data.as_of)})` : ""}. Only what Intercom shows:
        Slack- and back-office-based exceptions need those sources.
      </p>
      <Card className="overflow-x-auto">
        <div className="border-b px-3 py-2 text-sm font-medium">
          Player waiting for an answer · {data.unanswered.length}
          <div className="text-xs font-normal text-muted-foreground">
            The player's last message has no VIP-manager reply after it in any of their threads.
          </div>
        </div>
        <table className="w-full text-sm">
          <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Player wrote</th>
              <th className="px-3 py-2 font-medium">Player</th>
              <th className="px-3 py-2 font-medium">VIP manager</th>
              <th className="px-3 py-2 font-medium">Last reply</th>
              <th className="px-3 py-2 font-medium">Message</th>
            </tr>
          </thead>
          <tbody>
            {data.unanswered.map((e) => (
              <tr key={e.thread_id} onClick={() => setOpenId(e.thread_id)}
                  className="cursor-pointer border-b last:border-0 hover:bg-muted/50">
                <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                  {fmtDate(e.player_message_at)}
                  <div className="font-mono text-[11px]">{e.thread_id}</div>
                </td>
                <td className="px-3 py-2 text-xs">{e.contact_name || e.contact_email || "—"}</td>
                <td className="px-3 py-2 text-xs">{e.agent_name || "unassigned"}</td>
                <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                  {e.last_manager_reply_at ? fmtDate(e.last_manager_reply_at) : "never"}
                </td>
                <td className="max-w-md truncate px-3 py-2 text-xs text-muted-foreground" title={e.snippet}>{e.snippet}</td>
              </tr>
            ))}
            {!data.unanswered.length && (
              <tr><td colSpan={5} className="px-3 py-4 text-center text-xs text-muted-foreground">None.</td></tr>
            )}
          </tbody>
        </table>
      </Card>
      <Card className="overflow-x-auto">
        <div className="border-b px-3 py-2 text-sm font-medium">
          Thread with no owner · {data.no_owner.length}
        </div>
        <table className="w-full text-sm">
          <tbody>
            {data.no_owner.map((e) => (
              <tr key={e.thread_id} onClick={() => setOpenId(e.thread_id)}
                  className="cursor-pointer border-b last:border-0 hover:bg-muted/50">
                <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                  {fmtDate(e.created_at)}
                  <div className="font-mono text-[11px]">{e.thread_id}</div>
                </td>
                <td className="px-3 py-2 text-xs">{e.contact_name || e.contact_email || "—"}</td>
                <td className="px-3 py-2 text-xs">{e.topic || "—"}</td>
                <td className="max-w-md truncate px-3 py-2 text-xs text-muted-foreground">{e.subject || "—"}</td>
              </tr>
            ))}
            {!data.no_owner.length && (
              <tr><td className="px-3 py-4 text-center text-xs text-muted-foreground">None.</td></tr>
            )}
          </tbody>
        </table>
      </Card>
      {openId && (
        <ConversationDrawer id={openId} onClose={() => setOpenId(null)} readOnly
                            detailUrl={`/api/vip/conversations/${openId}`}
                            noGradeText="Threads are graded as part of their case — see Cases." />
      )}
    </div>
  );
}
