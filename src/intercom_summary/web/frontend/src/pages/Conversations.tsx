import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Search, Trash2, ChevronDown, Sparkles, RotateCcw, Archive, X } from "lucide-react";
import { api, ConversationList, TrashItem } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Badge, Button, Card, Input, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import RunDialog from "@/components/RunDialog";
import CsatBadge from "@/components/CsatBadge";
import { fmtDate, scoreColor } from "@/lib/utils";

const PAGE = 50;

export default function Conversations() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const writer = canWrite(user?.role);

  const [search, setSearch] = useState("");
  const [agent, setAgent] = useState("");
  const [agentText, setAgentText] = useState("");
  const [state, setState] = useState("");
  const [tag, setTag] = useState("");
  const [sort, setSort] = useState("created_at:desc");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [showDeleteMenu, setShowDeleteMenu] = useState(false);
  const [qaIds, setQaIds] = useState<string[] | null>(null);
  const [lastDeleted, setLastDeleted] = useState<{ ids: string[]; label: string } | null>(null);
  const [showTrash, setShowTrash] = useState(false);

  // The effective agent filter: dropdown value takes precedence over text input
  const effectiveAgent = agent || agentText.trim();

  const [sortField, sortDir] = sort.includes(":") ? sort.split(":") : [sort, "desc"];

  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (effectiveAgent) params.set("agent", effectiveAgent);
  if (state) params.set("state", state);
  if (tag) params.set("tag", tag);
  if (since) params.set("since", since);
  if (until) params.set("until", until + "T23:59:59");
  params.set("sort", sortField);
  if (sortDir === "asc") params.set("descending", "false");
  params.set("limit", String(PAGE));
  params.set("offset", String(offset));

  // Export mirrors the on-screen filters (agent/date/state/search/tag) but not pagination,
  // so the XLSX matches the visible list rather than dumping everything for the agent.
  const exportParams = new URLSearchParams();
  if (search) exportParams.set("search", search);
  if (effectiveAgent) exportParams.set("agent", effectiveAgent);
  if (state) exportParams.set("state", state);
  if (tag) exportParams.set("tag", tag);
  if (since) exportParams.set("since", since);
  if (until) exportParams.set("until", until + "T23:59:59");

  const { data, isLoading } = useQuery({
    queryKey: ["conversations", params.toString()],
    queryFn: () => api.get<ConversationList>(`/api/conversations?${params.toString()}`),
  });

  const { data: agentsData } = useQuery({
    queryKey: ["agents"],
    queryFn: () => api.get<{ agents: string[] }>("/api/agents"),
  });

  const { data: tagsData } = useQuery({
    queryKey: ["tags"],
    queryFn: () => api.get<{ tags: string[] }>("/api/tags"),
  });

  const knownAgents = agentsData?.agents ?? [];
  const knownTags = tagsData?.tags ?? [];

  const resetFilters = () => {
    setSearch("");
    setAgent("");
    setAgentText("");
    setState("");
    setTag("");
    setSince("");
    setUntil("");
    setOffset(0);
  };

  const toggleRow = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    const ids = data?.items.map((c) => c.id) ?? [];
    if (ids.every((id) => selected.has(id))) {
      setSelected((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    } else {
      setSelected((prev) => new Set([...prev, ...ids]));
    }
  };

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["conversations"] });
    qc.invalidateQueries({ queryKey: ["overview"] });
    qc.invalidateQueries({ queryKey: ["agents"] });
    qc.invalidateQueries({ queryKey: ["trash"] });
  };

  const { data: trashData } = useQuery({
    queryKey: ["trash"],
    queryFn: () => api.get<{ items: TrashItem[]; total: number }>("/api/trash"),
    enabled: writer,
  });
  const trashCount = trashData?.total ?? 0;

  // Filter object the delete endpoint understands (mirrors every active filter).
  const currentFilters = (): Record<string, unknown> => {
    const f: Record<string, unknown> = {};
    if (search) f.search = search;
    if (effectiveAgent) f.agent = [effectiveAgent];
    if (state) f.state = state;
    if (tag) f.tag = tag;
    if (since) f.since = since;
    if (until) f.until = until + "T23:59:59";
    return f;
  };
  const hasFilters = !!(search || effectiveAgent || state || tag || since || until);

  const runDelete = async (body: Record<string, unknown>, label: string) => {
    setDeleting(true);
    setShowDeleteMenu(false);
    try {
      const res = await api.post<{ deleted: number; ids: string[] }>(
        "/api/conversations/delete", body,
      );
      setSelected(new Set());
      setLastDeleted(res.ids?.length ? { ids: res.ids, label } : null);
      invalidate();
    } finally {
      setDeleting(false);
    }
  };

  // Count what a filter set matches (server-side), then confirm before deleting.
  const confirmAndDelete = async (
    qs: URLSearchParams, body: Record<string, unknown>, label: string,
  ) => {
    setShowDeleteMenu(false);
    qs.set("limit", "1");
    const res = await api.get<ConversationList>(`/api/conversations?${qs.toString()}`);
    if (res.total === 0) { alert(`No conversations match: ${label}.`); return; }
    if (!confirm(
      `Delete ${res.total.toLocaleString()} conversation(s) — ${label}?\n` +
      `They move to Trash and can be restored.\n` +
      `This clears the local cache only — a future Intercom fetch may import them again.`
    )) return;
    runDelete(body, label);
  };

  const filtersToQuery = (f: Record<string, unknown>) => {
    const qs = new URLSearchParams();
    Object.entries(f).forEach(([k, v]) => {
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, String(x)));
      else qs.set(k, String(v));
    });
    return qs;
  };

  const deleteOne = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(
      "Delete this conversation?\nIt moves to Trash and can be restored.\n" +
      "While it is in the Trash, Intercom fetches will not re-import it."
    )) return;
    await api.delete(`/api/conversations/${id}`);
    setSelected((prev) => { const n = new Set(prev); n.delete(id); return n; });
    setLastDeleted({ ids: [id], label: "1 conversation" });
    invalidate();
  };

  const deleteSelected = () => {
    if (!selected.size) return;
    if (!confirm(
      `Delete ${selected.size} selected conversation(s)?\n` +
      `They move to Trash and can be restored.\n` +
      `While they are in the Trash, Intercom fetches will not re-import them.`
    )) return;
    runDelete({ ids: [...selected] }, `${selected.size} selected`);
  };
  const deleteFiltered = () =>
    confirmAndDelete(filtersToQuery(currentFilters()), currentFilters(), "current filters");
  const deleteOlderThan = (days: number) => {
    const until = new Date(Date.now() - days * 86400_000).toISOString();
    confirmAndDelete(new URLSearchParams({ until }), { until }, `older than ${days} days`);
  };
  const deleteUngraded = () =>
    confirmAndDelete(new URLSearchParams({ ungraded: "true" }), { ungraded: true }, "ungraded");
  const deleteAll = () =>
    confirmAndDelete(new URLSearchParams(), { all: true }, "ALL conversations");

  const undoDelete = async () => {
    if (!lastDeleted) return;
    await api.post("/api/trash/restore", { ids: lastDeleted.ids });
    setLastDeleted(null);
    invalidate();
  };

  const allOnPageSelected =
    (data?.items.length ?? 0) > 0 && (data?.items ?? []).every((c) => selected.has(c.id));
  const moreThanPage = (data?.total ?? 0) > (data?.items.length ?? 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Conversations</h1>
        <div className="flex gap-2">
          {writer && selected.size > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setQaIds([...selected])}
            >
              <Sparkles className="h-4 w-4" /> Run QA on selected ({selected.size})
            </Button>
          )}
          {writer && (
            <div className="relative">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowDeleteMenu((v) => !v)}
                disabled={deleting}
              >
                <Trash2 className="h-4 w-4" />
                {deleting ? "Deleting…" : "Delete"}
                <ChevronDown className="h-3 w-3" />
              </Button>
              {showDeleteMenu && (
                <div
                  className="absolute right-0 top-full z-20 mt-1 w-64 rounded-md border bg-card py-1 shadow-lg"
                  onMouseLeave={() => setShowDeleteMenu(false)}
                >
                  {selected.size > 0 && (
                    <button
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                      onClick={deleteSelected}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      Delete selected ({selected.size})
                    </button>
                  )}
                  {hasFilters && (
                    <button
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                      onClick={deleteFiltered}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      Delete all matching filters{data ? ` (${data.total})` : ""}
                    </button>
                  )}
                  <div className="my-1 border-t" />
                  <div className="px-3 py-1 text-[10px] font-semibold uppercase text-muted-foreground">Quick clean-up</div>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                    onClick={() => deleteOlderThan(30)}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                    Older than 30 days
                  </button>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                    onClick={() => deleteOlderThan(90)}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                    Older than 90 days
                  </button>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                    onClick={deleteUngraded}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                    Ungraded conversations
                  </button>
                  <div className="my-1 border-t" />
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10"
                    onClick={deleteAll}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Delete ALL conversations
                  </button>
                </div>
              )}
            </div>
          )}
          {writer && (
            <Button variant="outline" size="sm" onClick={() => setShowTrash(true)}>
              <Archive className="h-4 w-4" /> Trash{trashCount > 0 ? ` (${trashCount})` : ""}
            </Button>
          )}
          <a href={`/api/export/conversations.xlsx?${exportParams.toString()}`}>
            <Button variant="outline" size="sm">
              <Download className="h-4 w-4" /> Export XLSX
            </Button>
          </a>
        </div>
      </div>

      {/* Undo banner after a delete */}
      {lastDeleted && (
        <div className="flex items-center justify-between gap-3 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-sm">
          <span className="text-muted-foreground">
            Deleted <span className="font-medium text-foreground">{lastDeleted.label}</span> — moved to Trash.
          </span>
          <div className="flex items-center gap-1">
            <Button variant="outline" size="sm" onClick={undoDelete}>
              <RotateCcw className="h-3.5 w-3.5" /> Undo
            </Button>
            <button onClick={() => setLastDeleted(null)} className="rounded p-1 text-muted-foreground hover:bg-muted">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Select-all-matching → cross-page delete */}
      {writer && allOnPageSelected && moreThanPage && (
        <div className="flex items-center justify-between gap-3 rounded-md border bg-muted/40 px-3 py-2 text-sm">
          <span className="text-muted-foreground">
            All {data?.items.length} on this page selected.
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={hasFilters ? deleteFiltered : deleteAll}
            disabled={deleting}
          >
            <Trash2 className="h-3.5 w-3.5 text-destructive" />
            Delete all {(data?.total ?? 0).toLocaleString()} matching
          </Button>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="w-64 pl-8"
            placeholder="Search by ID, subject, or customer…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setOffset(0); }}
          />
        </div>

        {/* Agent filter: dropdown when agents known, text fallback always available */}
        {knownAgents.length > 0 ? (
          <select
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            value={agent}
            onChange={(e) => { setAgent(e.target.value); setAgentText(""); setOffset(0); }}
          >
            <option value="">All agents</option>
            {knownAgents.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        ) : (
          <Input
            className="w-48"
            placeholder="Filter by agent…"
            value={agentText}
            onChange={(e) => { setAgentText(e.target.value); setAgent(""); setOffset(0); }}
          />
        )}

        <select
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          value={state}
          onChange={(e) => { setState(e.target.value); setOffset(0); }}
        >
          <option value="">Any state</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
          <option value="snoozed">Snoozed</option>
        </select>
        <select
          className="h-9 rounded-md border border-input bg-background px-3 text-sm"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
        >
          <option value="created_at:desc">Newest first</option>
          <option value="created_at:asc">Oldest first</option>
          <option value="score:asc">Lowest score</option>
          <option value="score:desc">Highest score</option>
          <option value="graded_at:asc">Oldest graded</option>
          <option value="graded_at:desc">Recently graded</option>
          <option value="messages">Most messages</option>
        </select>
        {knownTags.length > 0 && (
          <select
            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            value={tag}
            onChange={(e) => { setTag(e.target.value); setOffset(0); }}
          >
            <option value="">All tags</option>
            {knownTags.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        )}
        <Input
          type="date"
          className="h-9 w-36 px-2 text-sm"
          title="From date"
          value={since}
          max={until || undefined}
          onChange={(e) => { setSince(e.target.value); setOffset(0); }}
        />
        <Input
          type="date"
          className="h-9 w-36 px-2 text-sm"
          title="To date"
          value={until}
          min={since || undefined}
          onChange={(e) => { setUntil(e.target.value); setOffset(0); }}
        />
        {(search || effectiveAgent || state || tag || since || until) && (
          <Button variant="ghost" size="sm" onClick={resetFilters}>
            Clear filters
          </Button>
        )}
      </div>

      {/* Table */}
      <Card className="overflow-hidden">
        {isLoading || !data ? (
          <div className="flex h-48 items-center justify-center">
            <Spinner className="h-6 w-6 text-primary" />
          </div>
        ) : data.items.length === 0 ? (
          <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
            No conversations. Fetch some from the Overview page.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                {writer && (
                  <th className="w-8 px-3 py-2.5">
                    <input
                      type="checkbox"
                      className="rounded"
                      checked={allOnPageSelected}
                      onChange={toggleAll}
                    />
                  </th>
                )}
                <th className="px-4 py-2.5 font-medium">Subject</th>
                <th className="px-4 py-2.5 font-medium">Agent</th>
                <th className="px-4 py-2.5 font-medium">Customer</th>
                <th className="px-4 py-2.5 font-medium">State</th>
                <th className="px-4 py-2.5 font-medium">Msgs</th>
                <th className="px-4 py-2.5 font-medium">CSAT</th>
                <th className="px-4 py-2.5 font-medium">Created</th>
                <th className="px-4 py-2.5 font-medium">Graded</th>
                <th className="px-4 py-2.5 text-right font-medium">Score</th>
                {writer && <th className="w-8 px-3 py-2.5" />}
              </tr>
            </thead>
            <tbody>
              {data.items.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => setOpenId(c.id)}
                  className="cursor-pointer border-b last:border-0 hover:bg-muted/50"
                >
                  {writer && (
                    <td className="px-3 py-2.5" onClick={(e) => { e.stopPropagation(); toggleRow(c.id); }}>
                      <input
                        type="checkbox"
                        className="rounded"
                        checked={selected.has(c.id)}
                        onChange={() => toggleRow(c.id)}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </td>
                  )}
                  <td className="max-w-xs px-4 py-2.5">
                    <div className="truncate">{c.subject || "(no subject)"}</div>
                    {c.custom_tags && (
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        {c.custom_tags.split(",").filter(Boolean).map((t) => (
                          <span key={t} className="rounded-full bg-primary/10 px-1.5 py-0 text-[10px] font-medium text-primary">
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5">{c.agent_name}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{c.customer_name || c.customer_email}</td>
                  <td className="px-4 py-2.5">
                    <Badge className="border-border">{c.state}</Badge>
                  </td>
                  <td className="px-4 py-2.5">{c.message_count}</td>
                  <td className="px-4 py-2.5"><CsatBadge rating={c.csat_rating} /></td>
                  <td className="px-4 py-2.5 text-muted-foreground">{fmtDate(c.created_at)}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{fmtDate(c.graded_at)}</td>
                  <td className={`px-4 py-2.5 text-right font-semibold ${scoreColor(c.score)}`}>
                    {c.grade_dispute_status === "open" && (
                      <span className="mr-1 text-amber-500" title="Grade disputed">⚖</span>
                    )}
                    {c.score ?? "—"}
                  </td>
                  {writer && (
                    <td className="px-3 py-2.5">
                      <button
                        onClick={(e) => deleteOne(c.id, e)}
                        className="rounded p-1 text-muted-foreground opacity-0 hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100 [tr:hover_&]:opacity-100"
                        title="Delete conversation"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Pagination */}
      {data && data.total > PAGE && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + PAGE >= data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {openId && <ConversationDrawer id={openId} onClose={() => setOpenId(null)} />}

      {qaIds && (
        <RunDialog
          kind="review"
          conversationIds={qaIds}
          onClose={() => setQaIds(null)}
          onDone={() => {
            setQaIds(null);
            setSelected(new Set());
            qc.invalidateQueries({ queryKey: ["conversations"] });
            qc.invalidateQueries({ queryKey: ["overview"] });
          }}
        />
      )}

      {showTrash && (
        <TrashModal items={trashData?.items ?? []} total={trashCount}
          onClose={() => setShowTrash(false)} onChanged={invalidate} />
      )}
    </div>
  );
}

function TrashModal({
  items, total, onClose, onChanged,
}: {
  items: TrashItem[];
  total: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);

  const act = async (path: string, body: Record<string, unknown>, confirmMsg?: string) => {
    if (confirmMsg && !confirm(confirmMsg)) return;
    setBusy(true);
    try {
      await api.post(path, body);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-lg border bg-card shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Archive className="h-4 w-4" /> Trash ({total.toLocaleString()})
            {items.length < total && (
              <span className="font-normal text-muted-foreground">
                — showing the {items.length.toLocaleString()} most recent
              </span>
            )}
          </h2>
          <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-muted">
            <X className="h-4 w-4" />
          </button>
        </div>

        {items.length > 0 && (
          <div className="flex gap-2 border-b px-4 py-2">
            <Button size="sm" variant="outline" disabled={busy}
              onClick={() => act("/api/trash/restore", { all: true })}>
              <RotateCcw className="h-3.5 w-3.5" /> Restore all
            </Button>
            <Button size="sm" variant="outline" disabled={busy}
              className="text-destructive"
              onClick={() => act("/api/trash/purge", { all: true }, "Permanently delete everything in Trash? This cannot be undone.")}>
              <Trash2 className="h-3.5 w-3.5" /> Empty trash
            </Button>
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {items.length === 0 ? (
            <p className="p-8 text-center text-sm text-muted-foreground">Trash is empty.</p>
          ) : (
            <ul className="divide-y">
              {items.map((it) => (
                <li key={it.conversation_id} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{it.subject || `#${it.conversation_id}`}</div>
                    <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                      {it.agent_name && <span>{it.agent_name}</span>}
                      <span>· deleted {fmtDate(it.deleted_at)} by {it.deleted_by}</span>
                      {it.blacklist === 1 && (
                        <span
                          className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600 dark:text-amber-400"
                          title="Intercom fetches will not re-import this conversation while it is in the Trash."
                        >
                          blocked from re-import
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    className="rounded-md border px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
                    disabled={busy}
                    onClick={() => act("/api/trash/restore", { ids: [it.conversation_id] })}
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                  </button>
                  <button
                    className="rounded-md border px-2 py-1 text-xs text-destructive hover:bg-destructive/10 disabled:opacity-50"
                    disabled={busy}
                    onClick={() => act("/api/trash/purge", { ids: [it.conversation_id] }, "Permanently delete this conversation?")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
