import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { api, AgentLink } from "@/lib/api";
import { Button, Spinner } from "./ui/primitives";
import { X, Copy, Check, Trash2, Link2 } from "lucide-react";
import { fmtDate } from "@/lib/utils";

interface Props {
  agentName: string;
  onClose: () => void;
}

export default function GenerateLinkModal({ agentName, onClose }: Props) {
  const qc = useQueryClient();
  const [label, setLabel] = useState(`${agentName} — Review`);
  const [tag, setTag] = useState("");
  const [expiry, setExpiry] = useState<string>("30");
  const [newToken, setNewToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const { data: tagsData } = useQuery({
    queryKey: ["tags"],
    queryFn: () => api.get<{ tags: string[] }>("/api/tags"),
  });

  const { data: linksData, isLoading: linksLoading } = useQuery({
    queryKey: ["agent-links", agentName],
    queryFn: () => api.get<{ items: AgentLink[] }>(`/api/agent-links?agent=${encodeURIComponent(agentName)}`),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.post<AgentLink>("/api/agent-links", {
        agent_name: agentName,
        label: label.trim() || `${agentName} — Review`,
        tag: tag || null,
        expires_in_days: expiry === "never" ? null : parseInt(expiry),
      }),
    onSuccess: (link) => {
      setNewToken(link.token);
      qc.invalidateQueries({ queryKey: ["agent-links", agentName] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (token: string) => api.delete(`/api/agent-links/${token}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-links", agentName] }),
  });

  const reviewUrl = newToken
    ? `${window.location.origin}/review/${newToken}`
    : null;

  function copyUrl() {
    if (!reviewUrl) return;
    navigator.clipboard.writeText(reviewUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="flex w-full max-w-lg flex-col rounded-xl bg-background shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-5 py-4">
          <div className="flex items-center gap-2">
            <Link2 className="h-4 w-4 text-primary" />
            <h2 className="font-semibold">Generate Review Link</h2>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="space-y-4 overflow-auto p-5">
          {/* Agent */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Agent</label>
            <div className="rounded-md border bg-muted px-3 py-2 text-sm font-medium">{agentName}</div>
          </div>

          {/* Label */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Label <span className="font-normal opacity-70">(shown on the review page)</span>
            </label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
              placeholder={`${agentName} — Review`}
            />
          </div>

          {/* Tag filter */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">
              Tag filter <span className="font-normal opacity-70">(optional — limits conversations to this tag)</span>
            </label>
            <select
              value={tag}
              onChange={(e) => setTag(e.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="">— All conversations —</option>
              {tagsData?.tags.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          {/* Expiry */}
          <div>
            <label className="mb-1 block text-xs font-medium text-muted-foreground">Link expiry</label>
            <select
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
            >
              <option value="7">7 days</option>
              <option value="30">30 days</option>
              <option value="90">90 days</option>
              <option value="never">Never expires</option>
            </select>
          </div>

          <Button
            className="w-full"
            disabled={createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            {createMutation.isPending ? <Spinner className="h-4 w-4" /> : "Generate Link"}
          </Button>

          {/* Generated URL */}
          {reviewUrl && (
            <div className="rounded-lg border border-primary/30 bg-primary/5 p-3">
              <p className="mb-2 text-xs font-medium text-primary">Link generated — share this with the agent:</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 truncate rounded bg-muted px-2 py-1.5 text-xs">{reviewUrl}</code>
                <Button variant="outline" size="icon" onClick={copyUrl} title="Copy link">
                  {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
                </Button>
              </div>
            </div>
          )}

          {/* Existing links */}
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Existing links for this agent
            </p>
            {linksLoading ? (
              <div className="flex justify-center py-3">
                <Spinner className="h-4 w-4 text-primary" />
              </div>
            ) : !linksData?.items.length ? (
              <p className="text-xs text-muted-foreground">No links yet.</p>
            ) : (
              <div className="space-y-1.5">
                {linksData.items.map((link) => (
                  <div key={link.token} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                    <div className="min-w-0">
                      <div className="truncate font-medium">{link.label}</div>
                      <div className="text-xs text-muted-foreground">
                        {link.tag ? `tag: ${link.tag} · ` : ""}
                        created {fmtDate(link.created_at)}
                        {link.expires_at ? ` · expires ${fmtDate(link.expires_at)}` : " · no expiry"}
                      </div>
                    </div>
                    <div className="ml-2 flex shrink-0 items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        title="Copy link"
                        onClick={() => {
                          navigator.clipboard.writeText(`${window.location.origin}/review/${link.token}`);
                        }}
                      >
                        <Copy className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        title="Revoke link"
                        disabled={deleteMutation.isPending}
                        onClick={() => deleteMutation.mutate(link.token)}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
