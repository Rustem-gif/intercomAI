import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  MessagesSquare,
  Users,
  ScrollText,
  BarChart3,
  ClipboardCheck,
  AlertTriangle,
  Scale,
  BookOpen,
  GraduationCap,
  Moon,
  Sun,
  LogOut,
} from "lucide-react";
import { useTheme } from "@/lib/theme";
import { useAuth } from "@/lib/auth";
import { useGroup } from "@/lib/group";
import type { Group } from "@/lib/api";
import { Button } from "./ui/primitives";
import { cn } from "@/lib/utils";

const nav = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/conversations", label: "Conversations", icon: MessagesSquare },
  { to: "/needs-attention", label: "Needs Attention", icon: AlertTriangle },
  { to: "/disputes", label: "Grade Disputes", icon: Scale },
  { to: "/agents", label: "Agents", icon: Users },
  { to: "/evaluation", label: "Evaluation", icon: ClipboardCheck },
  { to: "/accuracy", label: "AI Accuracy", icon: BarChart3 },
  { to: "/knowledge-base", label: "Knowledge Base", icon: BookOpen },
  { to: "/coaching", label: "Coaching", icon: GraduationCap },
  { to: "/ruleset", label: "Ruleset", icon: ScrollText },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { theme, toggle } = useTheme();
  const { user, logout } = useAuth();
  const { group, setGroup } = useGroup();

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-card md:flex">
        <div className="flex h-14 items-center gap-2 border-b px-5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground text-xs font-bold">
            QA
          </div>
          <span className="font-semibold">Intercom QA</span>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-muted",
                )
              }
            >
              <n.icon className="h-4 w-4" />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t p-3 text-xs text-muted-foreground">
          <div className="px-2 py-1">
            Signed in as{" "}
            <span className="font-medium text-foreground">{user?.username}</span>
            <span className="ml-1 rounded bg-muted px-1.5 py-0.5">{user?.role}</span>
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b bg-card px-6">
          <div className="text-sm text-muted-foreground md:hidden">Intercom QA</div>
          <div className="ml-auto flex items-center gap-2">
            {/* Group switcher. VIP is graded against its own ruleset, so its scores are not
                comparable with standard ones — every page shows one group at a time. */}
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Users className="h-3.5 w-3.5" />
              <select
                className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                value={group}
                onChange={(e) => setGroup(e.target.value as Group)}
                title="Scope the dashboard to an agent group"
              >
                <option value="all">All agents</option>
                <option value="standard">Standard</option>
                <option value="vip">VIP</option>
              </select>
            </label>
            {group !== "all" && (
              <span className="rounded bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
                {group === "vip" ? "VIP ruleset" : "Standard ruleset"}
              </span>
            )}
            <Button variant="ghost" size="icon" onClick={toggle} title="Toggle theme">
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
            <Button variant="outline" size="sm" onClick={() => logout()}>
              <LogOut className="h-4 w-4" /> Sign out
            </Button>
          </div>
        </header>
        <main className="flex-1 overflow-auto p-6">{children}</main>
        <footer className="px-6 py-1.5 text-center text-[10px] text-muted-foreground/30 select-none">
          Created and administered by Rustem Samoilenko
        </footer>
      </div>
    </div>
  );
}
