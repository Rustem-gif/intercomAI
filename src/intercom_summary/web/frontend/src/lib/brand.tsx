// Active brand (All / King Billy / Tomb Riches / …) for the whole dashboard.
//
// One Intercom workspace serves several casino brands. They are separate products with
// separate players, so averaging their QA scores together describes neither — the brand tabs
// scope every page to one brand at a time.
//
// Like lib/group.tsx, the brand is applied inside lib/api.ts (setActiveBrand) rather than
// threaded through every page as a prop, so switching it invalidates the whole react-query
// cache and forces a refetch in the new scope.
//
// Unlike the group, the brand list is *dynamic*: it comes from whatever brands exist in the
// data (GET /api/brands), not a fixed enum. That means the persisted value can stop being
// valid — hence the reconciliation below.
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, setActiveBrand, type Brand, type BrandInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const STORAGE_KEY = "qa-active-brand";
export const ALL_BRANDS = "all";

const BrandContext = createContext<{
  brand: Brand;
  setBrand: (b: Brand) => void;
  brands: BrandInfo[];
  /** Brand tabs only earn their space once there is more than one brand to choose between. */
  showTabs: boolean;
}>({ brand: ALL_BRANDS, setBrand: () => {}, brands: [], showTabs: false });

function loadBrand(): Brand {
  return localStorage.getItem(STORAGE_KEY) || ALL_BRANDS;
}

export function BrandProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [brand, setBrandState] = useState<Brand>(loadBrand);

  // Apply the persisted brand before the first render's queries go out, so a reload doesn't
  // briefly fetch unscoped data.
  useEffect(() => {
    setActiveBrand(brand);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Skipped when signed out: the token-gated agent review portal renders under this provider
  // too, and /api/brands needs a session — without the guard every portal visit would fire a
  // pointless 401.
  const { user } = useAuth();
  const { data } = useQuery({
    queryKey: ["brands"],
    queryFn: () => api.get<{ brands: BrandInfo[] }>("/api/brands"),
    enabled: !!user,
  });
  const brands = data?.brands ?? [];

  const setBrand = useCallback(
    (b: Brand) => {
      setActiveBrand(b);
      localStorage.setItem(STORAGE_KEY, b);
      setBrandState(b);
      // Query keys don't include the brand, so drop the cache to refetch in the new scope.
      qc.invalidateQueries();
    },
    [qc],
  );

  // Self-heal: a persisted brand can disappear (its conversations were deleted, or this is a
  // different workspace). Falling back to "all" beats leaving someone on an empty dashboard
  // behind a filter whose tab is no longer on screen to un-select.
  useEffect(() => {
    if (!data || brand === ALL_BRANDS) return;
    if (!brands.some((b) => b.value === brand)) setBrand(ALL_BRANDS);
  }, [data, brands, brand, setBrand]);

  return (
    <BrandContext.Provider
      value={{ brand, setBrand, brands, showTabs: brands.length > 1 }}
    >
      {children}
    </BrandContext.Provider>
  );
}

export function useBrand() {
  return useContext(BrandContext);
}

// Stable per-brand accent, picked by position in the (count-ordered) brand list so a newly
// launched brand colours itself without a code change.
const BRAND_DOTS = [
  "bg-indigo-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-sky-500",
  "bg-rose-500",
  "bg-violet-500",
];

export function brandDot(value: string, brands: BrandInfo[]): string {
  const i = brands.findIndex((b) => b.value === value);
  return i < 0 ? "bg-muted-foreground" : BRAND_DOTS[i % BRAND_DOTS.length];
}

export function brandLabel(value: string, brands: BrandInfo[]): string {
  return brands.find((b) => b.value === value)?.label ?? value;
}
