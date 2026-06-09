// Thin fetch wrapper. Cookies carry the session, so always send credentials.

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
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
  tags: string;
  custom_tags: string;
  score: number | null;
  grade_summary: string | null;
  graded_at: string | null;
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

export interface ConversationDetail {
  conversation: any;
  transcript: string;
  grade: Grade | null;
  sla?: Sla;
  iconic: { conversation_id: string; added_by: string; added_at: string; manager_comment: string } | null;
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

export interface EvalStats {
  total: number;
  graded: number;
  pending: number;
  active_job: {
    id: string;
    status: string;
    result: any;
    error: string | null;
    created_at: string | null;
    cancellable: boolean;
  } | null;
}
