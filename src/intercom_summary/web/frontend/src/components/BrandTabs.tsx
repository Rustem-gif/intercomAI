import { ALL_BRANDS, brandDot, useBrand } from "@/lib/brand";
import { cn } from "@/lib/utils";

// Brand scope selector for the whole dashboard, sat directly under the header so the active
// scope is always on screen rather than hidden inside a collapsed dropdown.
//
// The tabs are built from whatever GET /api/brands returned — nothing here knows the name of
// any brand. A brand that appears in the data for the first time grows its own tab on the
// next refetch; one that vanishes takes its tab with it (lib/brand.tsx then resets the scope).
export default function BrandTabs() {
  const { brand, setBrand, brands, showTabs } = useBrand();

  // A single-brand workspace has nothing to switch between, so it gets no extra chrome. The
  // strip appears on its own the day a second brand shows up in the data.
  if (!showTabs) return null;

  const total = brands.reduce((sum, b) => sum + b.count, 0);

  const tab = (value: string, label: string, count: number, dot?: string) => (
    <button
      key={value}
      onClick={() => setBrand(value)}
      title={`Scope the dashboard to ${label}`}
      className={cn(
        "flex shrink-0 items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        brand === value
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-muted",
      )}
    >
      {dot && <span className={cn("h-2 w-2 rounded-full", dot)} />}
      {label}
      <span className="text-xs text-muted-foreground/70">{count.toLocaleString()}</span>
    </button>
  );

  return (
    <div className="flex items-center gap-1 overflow-x-auto border-b bg-card px-6 py-1.5">
      {tab(ALL_BRANDS, "All brands", total)}
      <span className="mx-1 h-4 w-px shrink-0 bg-border" />
      {brands.map((b) => tab(b.value, b.label, b.count, brandDot(b.value, brands)))}
    </div>
  );
}
