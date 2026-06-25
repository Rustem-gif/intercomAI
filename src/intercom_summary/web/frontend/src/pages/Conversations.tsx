import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Search, Trash2, ChevronDown, Sparkles } from "lucide-react";
import { api, ConversationList } from "@/lib/api";
import { useAuth, canWrite } from "@/lib/auth";
import { Badge, Button, Card, Input, Spinner } from "@/components/ui/primitives";
import ConversationDrawer from "@/components/ConversationDrawer";
import RunDialog from "@/components/RunDialog";
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
  };

  const deleteOne = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("Delete this conversation?")) return;
    await api.delete(`/api/conversations/${id}`);
    setSelected((prev) => { const n = new Set(prev); n.delete(id); return n; });
    invalidate();
  };

  const deleteBulk = async (ids: string[] | null, label: string) => {
    if (!confirm(`Delete ${label}? This cannot be undone.`)) return;
    setDeleting(true);
    setShowDeleteMenu(false);
    try {
      // null → delete all; [] → delete all (server interprets empty as "all")
      await api.post("/api/conversations/delete", { ids });
      setSelected(new Set());
      invalidate();
    } finally {
      setDeleting(false);
    }
  };

  const deleteSelected = () => deleteBulk([...selected], `${selected.size} selected conversation(s)`);
  const deleteFiltered = () => {
    // Re-query with current filters but no pagination to get all IDs
    const filterParams = new URLSearchParams();
    if (effectiveAgent) filterParams.set("agent", effectiveAgent);
    if (state) filterParams.set("state", state);
    if (search) filterParams.set("search", search);
    filterParams.set("limit", "10000");
    api.get<ConversationList>(`/api/conversations?${filterParams.toString()}`).then((res) => {
      const ids = res.items.map((c) => c.id);
      if (!ids.length) { alert("No conversations match the current filters."); return; }
      deleteBulk(ids, `${ids.length} filtered conversation(s)`);
    });
  };
  const deleteAll = () => deleteBulk(null, "ALL conversations");

  const allOnPageSelected =
    (data?.items.length ?? 0) > 0 && (data?.items ?? []).every((c) => selected.has(c.id));

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
                  className="absolute right-0 top-full z-20 mt-1 w-52 rounded-md border bg-card shadow-lg"
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
                  {(effectiveAgent || state || search) && (
                    <button
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                      onClick={deleteFiltered}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      Delete filtered results
                    </button>
                  )}
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
          <a href={`/api/export/conversations.xlsx${effectiveAgent ? `?agent=${encodeURIComponent(effectiveAgent)}` : ""}`}>
            <Button variant="outline" size="sm">
              <Download className="h-4 w-4" /> Export XLSX
            </Button>
          </a>
        </div>
      </div>

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
                  <td className="px-4 py-2.5 text-muted-foreground">{fmtDate(c.created_at)}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{fmtDate(c.graded_at)}</td>
                  <td className={`px-4 py-2.5 text-right font-semibold ${scoreColor(c.score)}`}>
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
    </div>
  );
}
