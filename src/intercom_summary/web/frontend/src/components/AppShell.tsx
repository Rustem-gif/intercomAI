import { useCallback, useEffect, useRef, useState } from "react";
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
import QwenIcon from "./QwenIcon";
import GlobalChat from "./GlobalChat";

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

const BTN = 56; // button size in px
const STORAGE_KEY = "qwen-btn-pos";

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

function defaultPos(): { x: number; y: number } {
  return { x: window.innerWidth - BTN - 20, y: window.innerHeight - BTN - 20 };
}

function loadPos(): { x: number; y: number } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const p = JSON.parse(raw);
      if (typeof p.x === "number" && typeof p.y === "number") return p;
    }
  } catch { /* ignore */ }
  return defaultPos();
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { theme, toggle } = useTheme();
  const { user, logout } = useAuth();
  const { group, setGroup } = useGroup();
  const [chatOpen, setChatOpen] = useState(false);

  // Draggable button position
  const [pos, setPos] = useState<{ x: number; y: number }>(loadPos);
  const posRef = useRef(pos);
  const dragging = useRef(false);
  const dragMoved = useRef(false);           // true if moved more than threshold
  const startMouse = useRef({ x: 0, y: 0 });
  const startPos = useRef({ x: 0, y: 0 });

  // Keep posRef in sync so the onPointerUp closure can read the latest value.
  useEffect(() => { posRef.current = pos; }, [pos]);

  // Re-clamp when window is resized.
  useEffect(() => {
    const onResize = () =>
      setPos((p) => ({
        x: clamp(p.x, 0, window.innerWidth - BTN),
        y: clamp(p.y, 0, window.innerHeight - BTN),
      }));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragging.current = true;
    dragMoved.current = false;
    startMouse.current = { x: e.clientX, y: e.clientY };
    startPos.current = { ...posRef.current };
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    if (!dragging.current) return;
    const dx = e.clientX - startMouse.current.x;
    const dy = e.clientY - startMouse.current.y;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) dragMoved.current = true;
    setPos({
      x: clamp(startPos.current.x + dx, 0, window.innerWidth  - BTN),
      y: clamp(startPos.current.y + dy, 0, window.innerHeight - BTN),
    });
  }, []);

  const onPointerUp = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(posRef.current));
  }, []);

  const onClick = useCallback(() => {
    // Suppress click when the user dragged the button.
    if (dragMoved.current) return;
    setChatOpen((v) => !v);
  }, []);

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

      {/* Draggable Qwen chat button */}
      <button
        style={{ left: pos.x, top: pos.y }}
        className={cn(
          "fixed z-50 touch-none select-none rounded-full shadow-lg",
          "transition-shadow hover:shadow-xl",
          chatOpen && "ring-2 ring-primary ring-offset-2",
        )}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onClick={onClick}
        title="Ask Qwen — drag to move"
        aria-label="Open Qwen chat"
      >
        <QwenIcon className="h-14 w-14" />
      </button>

      {chatOpen && <GlobalChat onClose={() => setChatOpen(false)} buttonPos={pos} />}
    </div>
  );
}
