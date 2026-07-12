// Active group (All / Standard / VIP) for the whole dashboard.
//
// VIP agents are graded against their own ruleset with its own criteria, so a VIP score and a
// standard score do not mean the same thing. Rather than blending them into one average, the
// switcher scopes every page to one group at a time.
//
// The group is applied inside lib/api.ts (setActiveGroup) rather than passed down as a prop, so
// switching it invalidates the whole react-query cache to force a refetch with the new scope.
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { setActiveGroup, type Group } from "@/lib/api";

const STORAGE_KEY = "qa-active-group";

const GroupContext = createContext<{
  group: Group;
  setGroup: (g: Group) => void;
}>({ group: "all", setGroup: () => {} });

function loadGroup(): Group {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === "vip" || raw === "standard" ? raw : "all";
}

export function GroupProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [group, setGroupState] = useState<Group>(loadGroup);

  // Apply the persisted group before the first render's queries go out, so a reload doesn't
  // briefly fetch unscoped data.
  useEffect(() => {
    setActiveGroup(group);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const setGroup = useCallback(
    (g: Group) => {
      setActiveGroup(g);
      localStorage.setItem(STORAGE_KEY, g);
      setGroupState(g);
      // Query keys don't include the group, so drop the cache to refetch in the new scope.
      qc.invalidateQueries();
    },
    [qc],
  );

  return (
    <GroupContext.Provider value={{ group, setGroup }}>{children}</GroupContext.Provider>
  );
}

export function useGroup() {
  return useContext(GroupContext);
}

export const GROUP_LABELS: Record<Group, string> = {
  all: "All agents",
  standard: "Standard",
  vip: "VIP",
};
