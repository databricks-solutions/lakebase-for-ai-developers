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
  [k: string]: unknown;
}

/** What the agent returns alongside the assistant text (from custom_outputs). */
export interface AgentExtras {
  route?: string;
  recommendation?: unknown;
  approval_request?: ApprovalRequest | null;
  status?: string;
  trace_notes?: string[];
  operational_sql?: string;
  [k: string]: unknown;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  extras?: AgentExtras;
  pending?: boolean;
  error?: boolean;
  steps?: string[]; // live progress labels while streaming
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
