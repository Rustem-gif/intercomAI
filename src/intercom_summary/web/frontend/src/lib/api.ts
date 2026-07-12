// Thin fetch wrapper. Cookies carry the session, so always send credentials.

// ── Group scoping (Standard / VIP) ───────────────────────────────────────────
// VIP agents are graded against a different ruleset, so their scores are not comparable with
// standard ones and the two are never averaged together. The AppShell switcher sets the active
// group here rather than threading a prop through every page; these GET endpoints honour it.
// Changing the group invalidates the react-query cache (see lib/group.tsx), so the pages refetch.
const GROUP_SCOPED = [
  "/api/overview",
  "/api/conversations",
  "/api/agents",
  "/api/agents/scores",
  "/api/accuracy",
  "/api/evaluation/stats",
];

export type Group = "all" | "standard" | "vip";

let activeGroup: Group = "all";

export function setActiveGroup(g: Group) {
  activeGroup = g;
}

function applyGroup(url: string): string {
  if (activeGroup === "all") return url;
  const path = url.split("?")[0];
  if (!GROUP_SCOPED.includes(path)) return url;
  return `${url}${url.includes("?") ? "&" : "?"}group=${activeGroup}`;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  if (method === "GET") url = applyGroup(url);
  const res = await fetch(url, {
    method,
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  // Guard: if the server returns HTML instead of JSON (e.g. SPA fallback
  // during a proxy hiccup) surface a meaningful error rather than a raw
  // JSON-parse crash.
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) {
    throw new ApiError(res.status, `Server returned non-JSON response (${ct || "no content-type"}). Is the backend running?`);
  }
  return res.json();
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const api = {
  get: <T>(url: string) => req<T>("GET", url),
  post: <T>(url: string, body?: unknown) => req<T>("POST", url, body),
  put: <T>(url: string, body?: unknown) => req<T>("PUT", url, body),
  delete: <T>(url: string, body?: unknown) => req<T>("DELETE", url, body),
};

// ── Types ────────────────────────────────────────────────────────────────────
export interface User {
  username: string;
  role: string;
}

export interface Admin {
  id: string;
  name: string;
  email: string;
}

export interface Overview {
  kpis: {
    conversations: number;
    graded: number;
    avg_score: number;
    violations: number;
    agents: number;
  };
  score_trend: { date: string; avg_score: number; count: number }[];
  top_violations: { text: string; count: number }[];
  agent_leaderboard: { agent: string; avg_score: number; count: number }[];
  worst_conversations: { id: string; agent: string; score: number; summary: string }[];
}

export interface AgentScores {
  start: string | null;
  end: string | null;
  since: string | null;
  until: string | null;
  agents: {
    agent: string;
    avg_score: number;
    count: number;
    avg_csat: number | null;
    csat_count: number;
    low_csat_count: number;
  }[];
}

export interface ConversationRow {
  id: string;
  agent_name: string;
  customer_name: string;
  customer_email: string;
  state: string;
  subject: string;
  created_at: string;
  message_count: number;
  csat_rating: number | null;
  grade_dispute_status: string | null;
  tags: string;
  custom_tags: string;
  score: number | null;
  grade_summary: string | null;
  graded_at: string | null;
}

export interface TrashItem {
  conversation_id: string;
  agent_name: string;
  subject: string;
  created_at: string;
  deleted_at: string;
  deleted_by: string;
}

export interface ConversationList {
  items: ConversationRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface RuleResult {
  rule_id: string;
  title: string;
  verdict: string;
  evidence: string;
  comment: string;
  /** Canonical points deducted when this criterion fails (present only for known QA criteria). */
  deduction?: number;
  /** True for critical criteria — a fail forces the overall score to 0. */
  critical?: boolean;
}

export interface Grade {
  conversation_id: string;
  agent_name: string;
  overall_score: number;
  summary: string;
  rule_results: RuleResult[];
  violations: string[];
  suggestions: string[];
  // Human override fields (null if not overridden)
  human_score: number | null;
  override_reason: string | null;
  overridden_by: string | null;
  overridden_at: string | null;
  /** Analyst per-criterion changes vs the AI ({criterion_id: verdict}); null if none. */
  human_criteria: Record<string, string> | null;
  /** Analyst manual deductions for things the AI can't verify (e.g. information correctness). */
  human_deductions: ManualDeduction[] | null;
}

export interface ManualDeduction {
  category: string;
  points: number;
  note: string;
}

export interface ManualDeductionPreset {
  id: string;
  label: string;
  description: string;
}

export interface Sla {
  first_response_time: number | null;
  first_response_time_human: string;
  time_to_close: number | null;
  time_to_close_human: string;
  first_response_target: number;
  followup_target: number;
  first_response_breached: boolean;
}

export interface Comment {
  id: string;
  conversation_id: string;
  author: string;
  text: string;
  created_at: string;
}

export interface GradeDispute {
  conversation_id: string;
  agent_name: string;
  reason: string;
  created_via: string;          // "portal" | "dashboard"
  created_by: string;
  created_at: string;
  status: string;               // "open" | "accepted" | "rejected"
  resolution_note?: string | null;
  resolved_by?: string | null;
  resolved_at?: string | null;
  // Present only on the manager-queue listing (joined from the conversation/grade).
  subject?: string | null;
  score?: number | null;
}

export interface ConversationDetail {
  conversation: any;
  transcript: string;
  grade: Grade | null;
  sla?: Sla;
  iconic: { conversation_id: string; added_by: string; added_at: string; manager_comment: string } | null;
  grade_dispute?: GradeDispute | null;
}

export interface Job {
  id: string;
  kind: string;
  status: string;
  result: any;
  error: string | null;
}

export interface JobListItem {
  id: string;
  kind: string;
  status: string;
  result: any;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface IconicCase {
  conversation_id: string;
  added_by: string;
  added_at: string;
  manager_comment: string;
  /** True when the source conversation was deleted — the case shows from its frozen snapshot. */
  archived?: boolean;
  conversation: {
    id: string;
    agent_name: string;
    customer_name: string;
    subject: string;
    state: string;
    created_at: string;
    score: number | null;
  } | null;
}

export interface AgentLink {
  token: string;
  agent_name: string;
  tag: string | null;
  label: string;
  created_by: string;
  created_at: string;
  expires_at: string | null;
}

export interface CoachingSession {
  id: string;
  agent_name: string;
  title: string;
  notes: string;
  due_date: string | null;
  status: "open" | "done";
  created_by: string;
  created_at: string;
  updated_at: string;
  item_count?: number;
  items?: CoachingItem[];
}

export interface CoachingItem {
  session_id: string;
  conversation_id: string;
  note: string;
  conversation: {
    id: string;
    agent_name: string;
    customer_name: string;
    subject: string;
    state: string;
    created_at: string;
    score: number | null;
  } | null;
}

export interface ReviewPortal {
  mode: "review" | "coaching";
  agent_name: string;
  label: string;
  tag: string | null;
  expires_at: string | null;
  // review mode
  conversations: ConversationRow[];
  total: number;
  // coaching mode
  session: {
    id: string;
    title: string;
    notes: string;
    due_date: string | null;
    status: "open" | "done";
  } | null;
  items: CoachingItem[];
}

export interface OllamaHealth {
  reachable: boolean;
  models: string[];
  error: string | null;
}

export interface OllamaRestart {
  ok: boolean;
  reachable: boolean;
  message: string;
}

export interface RulesetCriterion {
  id: string;
  title: string;
  deduction: number;
  critical?: boolean;
}

export interface QaRuleset {
  id: string;                       // "default" | "vip"
  name: string;
  version: string;
  criteria: RulesetCriterion[];
  manual_deductions: ManualDeductionPreset[];
  /** Places where the prompt text and the criteria catalogue disagree on the points. */
  warnings: string[];
}

export interface EvalStats {
  total: number;
  graded: number;
  pending: number;
  /** Graded under an older version of their own ruleset (re-grading will refresh them). */
  stale?: number;
  /** Graded by a different ruleset than their agent's group uses today — e.g. an agent's
   *  history from before they joined VIP. Left alone on purpose; re-grade to convert. */
  wrong_ruleset?: number;
  /** Conversations excluded from grading by tag (spam, empty, test, Jira, Follow-Up, no request). */
  ignored?: number;
  active_job: {
    id: string;
    status: string;
    result: any;
    error: string | null;
    created_at: string | null;
    cancellable: boolean;
  } | null;
}
