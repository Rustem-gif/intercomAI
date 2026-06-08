import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Admin, api } from "@/lib/api";
import { Input, Spinner } from "./ui/primitives";
import { Check, Search } from "lucide-react";
import { cn } from "@/lib/utils";

// The identifier we pass to the backend (resolves by email or name).
const idOf = (a: Admin) => a.email || a.name;

export default function AgentMultiSelect({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [filter, setFilter] = useState("");
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["admins"],
    queryFn: () => api.get<{ admins: Admin[] }>("/api/intercom/admins"),
  });

  const admins = data?.admins ?? [];
  const shown = useMemo(() => {
    const f = filter.toLowerCase();
    return admins.filter((a) => a.name.toLowerCase().includes(f) || a.email.toLowerCase().includes(f));
  }, [admins, filter]);

  const toggle = (id: string) =>
    onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);

  if (isLoading) {
    return (
      <div className="flex h-24 items-center justify-center rounded-md border">
        <Spinner className="h-5 w-5 text-primary" />
      </div>
    );
  }
  if (isError) {
    // Fallback: plain text input so the dialog is still usable even when the
    // Intercom admin roster can't be fetched (backend down, token missing, etc.)
    const rawText = value.join(", ");
    return (
      <div className="space-y-1.5">
        <p className="text-xs text-destructive">
          Couldn't load agent list from Intercom ({(error as any)?.message || "error"}). Enter
          agent names or e-mails separated by commas.
        </p>
        <Input
          placeholder="e.g. alice@example.com, Bob Smith"
          value={rawText}
          onChange={(e) =>
            onChange(
              e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
        />
      </div>
    );
  }

  return (
    <div className="rounded-md border">
      <div className="relative border-b">
        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          className="border-0 pl-8 focus-visible:ring-0"
          placeholder={`Search ${admins.length} agents…`}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>
      <div className="flex items-center justify-between px-3 py-1.5 text-xs text-muted-foreground">
        <span>{value.length} selected</span>
        <div className="flex gap-3">
          <button className="hover:text-foreground" onClick={() => onChange(shown.map(idOf))}>
            Select all
          </button>
          <button className="hover:text-foreground" onClick={() => onChange([])}>
            Clear
          </button>
        </div>
      </div>
      <div className="max-h-52 overflow-auto">
        {shown.map((a) => {
          const id = idOf(a);
          const checked = value.includes(id);
          return (
            <button
              key={a.id || id}
              onClick={() => toggle(id)}
              className="flex w-full items-center gap-3 px-3 py-1.5 text-left text-sm hover:bg-muted"
            >
              <span
                className={cn(
                  "flex h-4 w-4 items-center justify-center rounded border",
                  checked ? "border-primary bg-primary text-primary-foreground" : "border-input",
                )}
              >
                {checked && <Check className="h-3 w-3" />}
              </span>
              <span className="min-w-0 flex-1 truncate">
                {a.name || a.email}
                {a.name && a.email && (
                  <span className="ml-2 text-xs text-muted-foreground">{a.email}</span>
                )}
              </span>
            </button>
          );
        })}
        {shown.length === 0 && (
          <p className="px-3 py-4 text-center text-sm text-muted-foreground">No matching agents.</p>
        )}
      </div>
    </div>
  );
}
