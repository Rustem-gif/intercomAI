import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, ShieldAlert } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// Is the data safely backed up right now? Shown on every page, to everyone: on 6 Nov 2026 the
// database was lost and the nightly backup had been failing unseen for ten weeks.
export interface BackupHealth {
  ok: boolean;
  problems: string[];
  last_ok_at: string | null;
  offsite: boolean;
  journal_last_sync_at: string | null;
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

export function useBackupHealth() {
  return useQuery({
    queryKey: ["backup-health"],
    queryFn: () => api.get<BackupHealth>("/api/health/backup"),
    refetchInterval: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
  });
}

export default function BackupBadge() {
  const { data, isError } = useBackupHealth();
  const { user } = useAuth();
  if (!data && !isError) return null;
  const ok = !!data?.ok && !isError;
  const title = ok
    ? `Last backup ${ago(data!.last_ok_at)}${data!.offsite ? " · copied offsite" : ""}` +
      (data!.journal_last_sync_at ? ` · QA actions synced ${ago(data!.journal_last_sync_at)}` : "")
    : isError
      ? "Backup status unavailable"
      : data!.problems.join("\n");
  const pill = (
    <span
      title={title}
      className={cn(
        "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        ok
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          : "bg-red-500/15 text-red-700 dark:text-red-400",
      )}
    >
      {ok ? <ShieldCheck className="h-3.5 w-3.5" /> : <ShieldAlert className="h-3.5 w-3.5" />}
      {ok ? `Backed up ${ago(data!.last_ok_at)}` : "Backup problem"}
    </span>
  );
  return user?.role === "admin" ? <Link to="/storage">{pill}</Link> : pill;
}

export function BackupBanner() {
  const { data } = useBackupHealth();
  if (!data || data.ok) return null;
  return (
    <div className="flex flex-wrap items-start gap-2 border-b border-red-500/40 bg-red-500/10 px-6 py-2 text-sm">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
      <div>
        <span className="font-medium text-red-700 dark:text-red-400">
          The data is not safely backed up.
        </span>{" "}
        <span className="text-muted-foreground">{data.problems.join(" · ")}</span>
      </div>
    </div>
  );
}
