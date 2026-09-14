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

// ── Brand scoping (multi-brand workspace) ────────────────────────────────────
// One Intercom workspace serves several casino brands. Two brands are two different
// products, so blending their conversations into one average says nothing useful about
// either — the brand tabs scope every page to one at a time, applied here for the same
// reason as the group above. `"all"` means every brand; any other value is a raw Intercom
// brand value (or the unbranded token) passed straight through to the API.
const BRAND_SCOPED = [...GROUP_SCOPED, "/api/export/conversations.xlsx"];

export type Brand = string; // "all" | raw Intercom brand value | "__unbranded__"

/** Filter token the API understands for conversations that carry no brand. */
export const UNBRANDED = "__unbranded__";

let activeBrand: Brand = "all";

export function setActiveBrand(b: Brand) {
  activeBrand = b;
}

/** Active brand as a request-body value: undefined when unscoped, so it can be spread in. */
export function activeBrandParam(): string | undefined {
  return activeBrand === "all" ? undefined : activeBrand;
}

function applyScope(url: string): string {
  const path = url.split("?")[0];
  const params: string[] = [];
  if (activeGroup !== "all" && GROUP_SCOPED.includes(path)) {
    params.push(`group=${encodeURIComponent(activeGroup)}`);
  }
  if (activeBrand !== "all" && BRAND_SCOPED.includes(path)) {
    params.push(`brand=${encodeURIComponent(activeBrand)}`);
  }
  if (!params.length) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${params.join("&")}`;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  if (method === "GET") url = applyScope(url);
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

/** One brand of the multi-brand workspace, as offered by the brand tabs. */
export interface BrandInfo {
  /** Raw Intercom brand value, or UNBRANDED. This is what the API filters on. */
  value: string;
  /** Display name — King Billy's raw value is "Betncare", so these differ. */
  label: string;
  count: number;
}

export interface ConversationRow {
  id: string;
  agent_name: string;
  /** Raw Intercom brand value; "" when unknown. */
  brand: string;
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
  blacklist: number;   // 1 = blocked from re-import by a future Intercom fetch
}

export interface StorageStats {
  db: {
    path: string;
    bytes: number;
    reclaimable_bytes: number;
    tables: { table: string; rows: number; approx_bytes: number }[];
  };
  trash: {
    total: number;
    blacklisted: number;
    bytes: number;
    oldest: string | null;
    newest: string | null;
    retention_days: number;
    expiring_now: number;
  };
  dirs: { exports_bytes: number; backups_bytes: number };
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
  /** Gated rulesets only (QA Manual v4.1): which of the three gates this criterion sits in. */
  gate?: number;
  /** Gated rulesets only: "critical" | "major" | "minor". A Major caps the score, not deducts. */
  severity?: string;
  /** Gated rulesets only: the capped block this criterion shares (e.g. "communication"). */
  group?: string;
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
  /** Which ruleset produced this grade — decides how a re-score is computed. */
  ruleset_id?: string;
  overall_result?: string;          // "PASS" | "FAIL"
  band?: string;

  // ── QA Manual v4.1 (gated rulesets only; absent on flat-ruleset grades) ──
  /** What became of the player's case, reported separately from how well the agent worked. */
  outcome_status?: string;
  severity?: string;                // Critical | Major | Minor
  /** Service that collapsed on every axis at once. NOT the same as a compliance breach. */
  catastrophic_service_failure?: boolean;
  critical_fail?: boolean;
  manual_review_needed?: boolean;
  manual_review_reason?: string;
  case_type?: string;
  risk_flag?: string;
  expected_handling?: string;
  data_sufficiency?: string;
  confidence?: string;
  /** Each thing the player asked for, and what became of it. */
  requests?: { text: string; status: string; material?: boolean }[];
}

/** What a set of verdicts would score. Computed server-side so there is only one copy of the
 *  formula — see POST /api/qa/preview-score. */
export interface ScorePreview {
  score: number;
  band: string;
  result: string;                   // "PASS" | "FAIL"
  pass_threshold: number;
  scoring_model: string;
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
  /** Scores this chat used to carry, newest first. Empty until a re-grade replaces one. */
  grade_history?: PastGrade[];
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
  /** The date range the link covers. Both null on links made before ranges existed. */
  since: string | null;
  until: string | null;
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
  /** The date range the link covers; both null means every conversation. */
  since: string | null;
  until: string | null;
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
  gate?: number;
  severity?: string;
  group?: string;
}

/** How a ruleset turns verdicts into a score. "flat" is 100 minus the deductions; "gated" is
 *  the v4.1 three-gate model, where a Major Outcome Failure caps the score instead. */
export interface RulesetScoring {
  model: string;                    // "flat" | "gated"
  pass_threshold: number;
  major_cap?: number;
  no_major_floor?: number;
  catastrophic_score?: number;
  group_caps?: Record<string, number>;
}

export interface QaRuleset {
  id: string;                       // "default" | "vip" | "kb-v41"
  name: string;
  version: string;
  criteria: RulesetCriterion[];
  scoring: RulesetScoring;
  manual_deductions: ManualDeductionPreset[];
  /** Places where the prompt text and the criteria catalogue disagree on the points. */
  warnings: string[];
}

/** What editing a ruleset's prompt would invalidate. Any edit bumps the version, which marks
 *  that ruleset's grades stale and re-grades them with different scores on the next run. */
export interface BlastRadius {
  graded_at_current_version: number;
  would_regrade: number;
  protected_by_human_review: number;
}

/** A score this conversation used to carry, before a re-grade replaced it. */
export interface PastGrade {
  archived_at: string;
  graded_at: string | null;
  overall_score: number | null;
  human_score: number | null;
  rules_version: string | null;
  ruleset_id: string | null;
  model: string | null;
}

export interface EvalStats {
  total: number;
  graded: number;
  pending: number;
  /** Graded under an older version of their own ruleset (re-grading will refresh them). */
  stale?: number;
  /** Re-graded by an analyst — never re-graded by an ordinary run, so never stale. */
  human_reviewed?: number;
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
