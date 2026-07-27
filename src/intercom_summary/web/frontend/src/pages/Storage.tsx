import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, StorageStats } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, Button, Spinner } from "@/components/ui/primitives";
import { fmtDate } from "@/lib/utils";
import { HardDrive, Trash2, Database, AlertTriangle } from "lucide-react";

function fmtBytes(n: number) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export default function Storage() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["storage"],
    queryFn: () => api.get<StorageStats>("/api/storage"),
  });

  const run = async (what: "vacuum" | "expire", confirmText: string) => {
    if (!confirm(confirmText)) return;
    setBusy(what);
    try {
      if (what === "vacuum") {
        const r = await api.post<{ freed_bytes: number }>("/api/storage/vacuum", {});
        alert(`Database compacted — reclaimed ${fmtBytes(r.freed_bytes)}.`);
      } else {
        const r = await api.post<{ purged: number }>("/api/storage/expire-trash", {});
        alert(`Purged ${r.purged} expired item(s) from the Trash.`);
      }
      qc.invalidateQueries({ queryKey: ["storage"] });
      qc.invalidateQueries({ queryKey: ["trash"] });
    } catch (e: any) {
      alert(e.message || "Failed");
    } finally {
      setBusy("");
    }
  };

  if (isLoading || !data)
    return (
      <div className="flex justify-center py-20">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );

  const { db, trash, dirs } = data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Storage</h1>
        <p className="text-sm text-muted-foreground">
          What the system is holding on disk, and what can be reclaimed.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="flex-row items-center gap-2">
            <Database className="h-4 w-4 text-muted-foreground" />
            <CardTitle>Database</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Stat label="File size" value={fmtBytes(db.bytes)} hint={db.path} />
            <Stat
              label="Reclaimable"
              value={fmtBytes(db.reclaimable_bytes)}
              hint="Free pages inside the file — recovered by compacting"
            />
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              disabled={busy === "vacuum"}
              onClick={() =>
                run("vacuum", "Compact the database?\nThis rewrites the file and may take a minute.")
              }
            >
              {busy === "vacuum" ? "Compacting…" : "Compact database (VACUUM)"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2">
            <Trash2 className="h-4 w-4 text-muted-foreground" />
            <CardTitle>Trash</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Stat
              label="Items"
              value={trash.total.toLocaleString()}
              hint={`${fmtBytes(trash.bytes)} of snapshots`}
            />
            <Stat
              label="Blocked from re-import"
              value={trash.blacklisted.toLocaleString()}
              hint="Deleted individually — Intercom fetches skip these"
            />
            <p className="text-xs text-muted-foreground">
              Retention: {trash.retention_days} days
              {trash.oldest && <> · oldest {fmtDate(trash.oldest)}</>}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              disabled={busy === "expire" || trash.expiring_now === 0}
              onClick={() =>
                run(
                  "expire",
                  `Permanently purge ${trash.expiring_now} item(s) older than ${trash.retention_days} days?\nThis cannot be undone.`,
                )
              }
            >
              {busy === "expire"
                ? "Purging…"
                : trash.expiring_now > 0
                  ? `Purge ${trash.expiring_now} expired item(s)`
                  : "Nothing expired"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2">
            <HardDrive className="h-4 w-4 text-muted-foreground" />
            <CardTitle>Files</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Stat label="Exports" value={fmtBytes(dirs.exports_bytes)} />
            <Stat label="Local backups" value={fmtBytes(dirs.backups_bytes)} />
          </CardContent>
        </Card>
      </div>

      {trash.blacklisted > 0 && (
        <div className="flex gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-4">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <div className="text-sm">
            <p className="font-medium text-amber-600 dark:text-amber-400">
              {trash.blacklisted.toLocaleString()} conversation(s) will not be re-imported
            </p>
            <p className="mt-1 text-muted-foreground">
              Conversations deleted individually stay blocked while they sit in the Trash, so an
              Intercom fetch covering their dates will import fewer conversations than it
              downloads. Restore or purge them from the Trash on the Conversations page to import
              them again.
            </p>
          </div>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Tables</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="pb-2 font-medium">Table</th>
                  <th className="pb-2 text-right font-medium">Rows</th>
                  <th className="pb-2 text-right font-medium">Payload size</th>
                </tr>
              </thead>
              <tbody>
                {db.tables.map((t) => (
                  <tr key={t.table} className="border-b last:border-0">
                    <td className="py-2 font-mono text-xs">{t.table}</td>
                    <td className="py-2 text-right tabular-nums">{t.rows.toLocaleString()}</td>
                    <td className="py-2 text-right tabular-nums text-muted-foreground">
                      {t.approx_bytes ? fmtBytes(t.approx_bytes) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
