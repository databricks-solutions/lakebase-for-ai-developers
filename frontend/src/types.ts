export interface Me {
  email: string;
  user_id: string | null;
  is_local: boolean;
  workspace_host: string;
  demo_planner_user: string | null;
  in_scope: boolean;
}

export interface ApprovalRequest {
  summary?: string;
  est_cost_usd?: number | null;
  actions?: unknown[];
  recommendation?: StructuredRecommendation;
  [k: string]: unknown;
}

// ── Shared HITL contract (matches the backend exactly) ─────────────────────────────────────
// The paused-run plan arrives on the chat stream's `done` frame inside `extras`. Canonical
// channel = `extras.recommendation`; evidence on `extras.evidence`; commit result on
// `extras.commit_ledger`.

export interface ActionFact {
  k: string;
  v: string;
  tone?: string;
}

export type ActionKind =
  | "expedite_po"
  | "split_source"
  | "raise_safety_stock"
  | "allocation_constraint"
  | "quality_hold"
  | "quarantine_po"
  | "tighten_inspection"
  | "supplier_quality_hold";

export interface PlannedAction {
  key: string;
  kind: ActionKind;
  title: string;
  detail: string;
  target_table: "approved_actions" | "planning_parameters" | "constraints";
  editable: boolean;
  qty?: number;
  qty_label?: string;
  qty_min?: number;
  qty_max?: number;
  qty_step?: number;
  facts?: ActionFact[];
  cost_delta?: number;
  evidence_refs?: string[];
  default_status?: "approve" | "hold";
  sku?: string;
  supplier_id?: string;
  po_id?: string;
  program?: string;
}

export interface StructuredRecommendation {
  summary: string;
  needs_approval: boolean;
  is_action_bearing?: boolean;
  est_cost_usd?: number | null;
  reasoning?: string | null;
  actions?: string[];
  planned_actions?: PlannedAction[];
  citations?: string[];
}

/** extras.evidence — the three gather agents' output, rendered as the three evidence cards. */
export interface EvidenceBundle {
  data?: Record<string, unknown>[]; // operational/analytics rows → "Data agent" card
  rag?: { source?: string; content?: string; score?: number }[]; // knowledge → "RAG agent" card
  memory?: { text: string; score?: number | null; namespace?: string }[]; // recalled → "Memory agent" card
}

/** extras.commit_ledger — what the resume actually wrote. */
export interface CommitLedger {
  approved_actions?: number;
  planning_parameters?: number;
  constraints?: number;
  rows?: unknown[];
  error?: string;
}

// Per-action resume payload sent on commit.
export interface ActionDecisionInput {
  key: string;
  status: "approve" | "hold";
  edited_qty?: number;
  safety_stock_override?: number;
}

export interface ResumeDecisions {
  verdict: "approved" | "rejected";
  rationale: string;
  action_decisions: ActionDecisionInput[];
}

/** What the agent returns alongside the assistant text (from custom_outputs). */
export interface AgentExtras {
  route?: string;
  recommendation?: StructuredRecommendation;
  approval_request?: ApprovalRequest | null;
  evidence?: EvidenceBundle;
  commit_ledger?: CommitLedger;
  status?: string;
  trace_notes?: string[];
  operational_sql?: string;
  trace_id?: string | null; // MLflow trace id — 👍/👎 feedback attaches to this
  [k: string]: unknown;
}

/** State-endpoint shape: GET /api/state/tables?thread_id=... */
export interface StateTableRow {
  [k: string]: unknown;
}

export interface RecalledMemory {
  text: string;
  score?: number | null;
  namespace?: string;
}

export interface StateTables {
  thread_id: string;
  approved_actions: StateTableRow[];
  planning_parameters: StateTableRow[];
  constraints: StateTableRow[];
  recalled_memory: RecalledMemory[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  extras?: AgentExtras;
  pending?: boolean;
  error?: boolean;
  steps?: string[]; // live progress labels while streaming
  route?: string[]; // gather agents chosen by the supervisor for this turn
}

export interface Session {
  thread_id: string;
  title?: string;
  preview?: string;
  updated_at?: string;
}

export interface ExplorerCard {
  key: string;
  title: string;
  subtitle: string;
  accent: "navy" | "lava" | "blue" | "green" | "yellow" | "maroon";
  facts: Record<string, string>;
  link?: string | null;
  link_label?: string;
  peek?: string;
}

export interface ExplorerData {
  workspace_host: string;
  cards: ExplorerCard[];
}
