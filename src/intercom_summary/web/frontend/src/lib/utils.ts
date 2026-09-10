import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function scoreColor(score: number | null | undefined): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 85) return "text-emerald-500";
  if (score >= 70) return "text-amber-500";
  return "text-destructive";
}

// An Intercom CSAT rating (1-5) <= this value counts as "low" and is surfaced in
// Needs Attention. Keep in sync with settings.csat_low_max on the backend.
export const CSAT_LOW_MAX = 1;

export function isLowCsat(rating: number | null | undefined): boolean {
  return rating != null && rating <= CSAT_LOW_MAX;
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Clock time only (HH:MM:SS) for individual chat messages.
export function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

// Human-readable gap, mirrors fmt_duration() on the backend: 45 → "45s", 492 → "8m 12s".
export function fmtGap(seconds?: number | null): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

// Seconds between two ISO timestamps, or null if either is missing.
export function gapSeconds(prevIso?: string | null, iso?: string | null): number | null {
  if (!prevIso || !iso) return null;
  return (new Date(iso).getTime() - new Date(prevIso).getTime()) / 1000;
}


/** A calendar day, with no time. `since`/`until` are date-only, so fmtDate's clock would be
 *  noise — and, being parsed as UTC midnight, actively misleading in a westward timezone. */
export function fmtDay(iso?: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** What a review link covers. Links made before ranges existed carry neither and show everything. */
export function linkRange(link: { since: string | null; until: string | null }): string {
  if (link.since && link.until) return `${fmtDay(link.since)} – ${fmtDay(link.until)}`;
  if (link.since) return `from ${fmtDay(link.since)}`;
  if (link.until) return `until ${fmtDay(link.until)}`;
  return "all dates";
}
