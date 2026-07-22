import type { CSSProperties, ReactNode } from "react";
import {
  Boxes,
  BrainCircuit,
  Check,
  Database,
  GitBranch,
  Layers,
  MessageSquare,
  Network,
  PauseCircle,
  Search,
  ShieldCheck,
} from "lucide-react";

/**
 * Standalone About / demo-overview view. Not a data page — pure narrative, modeled on the
 * HR Command Center's About page: hero → problem → demo flow (show/say/outcome) → why-Lakebase
 * → architecture. Copy is grounded in the demo-script talk track.
 */

interface Step {
  n: string;
  title: string;
  icon: ReactNode;
  show: string;
  say: string;
  outcome: string;
}

const STEPS: Step[] = [
  {
    n: "1",
    title: "Similarity + join in one query",
    icon: <Search size={15} />,
    show:
      'Ask "Henkel\'s SKU-1001 has recurring adhesive cracking — show me similar past cases joined to on-hand inventory and open POs, and recommend a mitigation." It surfaces similar incidents (on-hand 40, open POs 800, a 760-unit gap) and proposes actions right in the chat.',
    say:
      "The question asks for two things at once — a similarity search and a live join — and Lakebase does both in one pgvector query. quality_incidents.embedding is a real pgvector column with an HNSW index; inventory_current and open_pos are Delta tables Lakebase keeps mirrored into this same Postgres via Synced Tables. So the join is a plain Postgres join, not glue code.",
    outcome:
      "The core proof point: vector similarity as one predicate inside a governed relational query.",
  },
  {
    n: "2",
    title: "Two engines, different jobs",
    icon: <Layers size={15} />,
    show:
      'Ask "Nucor announced a carbon-steel price increase. Find related market-event notes and similar past incidents, and recommend whether to pre-buy."',
    say:
      "Vector Search reads the static document corpus — the market-event notes. Lakebase pgvector scans operational memory for similar past incidents. Different jobs, not competing. The trace shows both running in parallel and converging before the plan is drafted. This one's a read — nothing commits until a mitigation is approved.",
    outcome: "When to use Vector Search vs Lakebase, shown side by side.",
  },
  {
    n: "3",
    title: "All three engines, one trace",
    icon: <Network size={15} />,
    show:
      'Ask the SKU-1001 question again, extended: "…check their latest supplier notification, and roll up open POs by supplier for Q4." Lakebase (incident + inventory join), Vector Search (supplier notification), and Genie (open-PO rollup) all fire in one step.',
    say:
      "One question fans out to all three engines at once — Lakebase joins the incident to live inventory, Vector Search pulls the supplier notification from the document corpus, and Genie runs the NL→SQL rollup of open POs by supplier. The trace shows three overlapping spans converging on a single plan; the router decided which engine each sub-question belonged to.",
    outcome:
      "Genie for the structured NL→SQL rollup; the router picks the right engine per sub-question.",
  },
  {
    n: "4",
    title: "The write-back",
    icon: <Check size={15} />,
    show:
      'Ask "…put together a full containment plan that holds the on-hand lot, quarantines the incoming PO, tightens inspection, and holds Henkel until re-validated." Four structured actions, each independently approve / edit / hold-able in Review. Commit, then flip to Lakebase (Engineer lens).',
    say:
      "One commit lands three tables — approved_actions, planning_parameters, constraints — each action in the table that matches its kind. The row you're looking at is the same Postgres row your decision just wrote, no translation layer.",
    outcome:
      "Human-in-the-loop approval writing durable, governed state. Review is where the human decides; Lakebase is where you verify it stuck.",
  },
  {
    n: "5",
    title: "Cross-session memory",
    icon: <BrainCircuit size={15} />,
    show:
      'In a brand-new chat with no history, ask "What did we decide about the Henkel SKU-1001 containment plan?"',
    say:
      "It cites the approved plan back to you — the hold, the quarantine, the inspection tightening — pulled from the committed rows, not from chat history. The planner hydrates from Lakebase before answering. And it survives an app restart, because the decision lives in Postgres, not in-process memory.",
    outcome: "Long-term episodic memory recall — the payoff of the scenario-4 write-back.",
  },
  {
    n: "6",
    title: "Resume across a pause",
    icon: <PauseCircle size={15} />,
    show: 'Ask "Continue the Henkel escalation from this morning."',
    say:
      "LangGraph checkpoints every run to Lakebase, so a workflow paused mid-way — say, waiting on your Review approval — picks up exactly where it left off. One resumable MLflow trace spans the pause; the agent rehydrates full state from the checkpoint instead of restarting.",
    outcome:
      "Short-term thread state made durable — distinct from the episodic recall in step 5.",
  },
];

const WHY: { title: string; body: string }[] = [
  {
    title: "Not files",
    body: "File-based memory (markdown, etc.) doesn't scale and is hard to retrieve from efficiently.",
  },
  {
    title: "Not a vector-only store",
    body: "A vector-only store handles similarity but not the relational side — recall usually needs joins and filters across conversation_id, user, or group, which vector DBs handle poorly. Lakebase does both in one engine.",
  },
  {
    title: "Governance",
    body: "Same Databricks environment and access model as Unity Catalog; integrates directly with Databricks Apps for full-stack agents.",
  },
  {
    title: "Scale-to-zero",
    body: "Serverless autoscaling fits bursty, variable memory workloads without paying for idle.",
  },
  {
    title: "Branching",
    body: "Treat memory design like a codebase: keep Production sanitized, do real work on a Dev branch, fork throwaway branches to try new memory structures without touching prod.",
  },
];

const ARCH: { icon: ReactNode; label: string; body: string }[] = [
  {
    icon: <Network size={14} />,
    label: "Multi-agent graph",
    body: "LangGraph on Databricks Apps. A supervisor routes each question; a gather phase runs the Operational (Lakebase), Knowledge (Vector Search), and Analytics (Genie) agents in parallel; a planner drafts the recommendation; an interrupt() gate holds for human approval before commit.",
  },
  {
    icon: <Database size={14} />,
    label: "Lakebase Postgres",
    body: "The single durable backend. pgvector for similarity, Synced Tables mirroring operational Delta tables for the joins, approved_actions / planning_parameters / constraints for committed decisions.",
  },
  {
    icon: <BrainCircuit size={14} />,
    label: "Memory, two tiers",
    body: "Short-term is the conversation thread (LangGraph checkpointer keyed on thread_id); long-term is episodic (the actual past decision, written on approval) and semantic (the distilled preference behind it), both in Lakebase.",
  },
  {
    icon: <Boxes size={14} />,
    label: "MLflow",
    body: "End-to-end tracing with autolog; one resumable trace spans the HITL pause.",
  },
  {
    icon: <Layers size={14} />,
    label: "Databricks App",
    body: "React + Vite frontend, FastAPI backend, deployed via Databricks Asset Bundles.",
  },
];

const STACK = ["Lakebase", "Vector Search", "Genie", "LangGraph", "MLflow"];

export function AboutPanel() {
  return (
    <div style={{ animation: "fade-up var(--dur-base) var(--ease-out)" }}>
      <div style={{ maxWidth: 1040, margin: "0 auto", padding: "var(--space-5) var(--space-5) var(--space-8)" }}>
        {/* Hero */}
        <div style={{ ...eyebrow, fontSize: 9.5, marginBottom: 6 }}>MFG persona · Demo overview</div>
        <h1 style={{ margin: 0, fontSize: 30, letterSpacing: "-0.02em", lineHeight: 1.08 }}>
          Supply-Chain Planner Copilot
        </h1>
        <p style={{ fontSize: 14, color: "var(--fg-1)", margin: "12px 0 0", maxWidth: 660, lineHeight: 1.55 }}>
          A single Databricks app that gives a manufacturing planner one place to ask a recurring
          quality-issue question, get a recommendation grounded in live operational data, approve or
          edit it, and have that decision persist — remembered across sessions and resumable across
          restarts.
        </p>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 16 }}>
          {STACK.map((s) => (
            <span key={s} style={chip}>{s}</span>
          ))}
        </div>

        {/* Problem */}
        <Divider />
        <SectionHead icon={<ShieldCheck size={13} />}>The problem we're solving</SectionHead>
        <p style={prose}>
          A planner facing a recurring quality issue is really asking two questions at once — <i>"have
          we seen this before?"</i> and <i>"what's my exposure right now?"</i> Answering means a
          similarity lookup against past incidents, then a separate trip to the operational systems
          for on-hand inventory and open POs, then stitching the two together by hand — usually in a
          spreadsheet, usually re-done from scratch every time. The recommendation that comes out of
          it lives in someone's head or a Slack thread; the next planner with the same problem starts
          over.
        </p>
        <p style={prose}>
          Databricks collapses that into one query path. Lakebase runs the similarity search and the
          operational join in the same Postgres engine — the vector match is just one predicate inside
          an otherwise normal relational query — so <i>"similar past cases joined to on-hand inventory
          and open POs"</i> is a single statement, not app code gluing a vector DB to a database. The
          planner reviews the result, approves it, and the decision is written back as governed
          Postgres rows plus a memory the agent can recall later. The afternoon of stitching becomes
          one question; the decision stops evaporating.
        </p>

        {/* Demo flow */}
        <Divider />
        <SectionHead icon={<MessageSquare size={13} />}>Demo flow</SectionHead>
        <p style={{ ...prose, marginTop: 0 }}>
          Suggested 15–20 minute walkthrough. The first four are the welcome-screen scenario cards —
          ask them in order, each adds an engine, the last is the write-back. The final two are typed
          straight into chat to put memory itself on stage. The app is three tabs:{" "}
          <b>Chat → Review</b> (approve / edit / hold each action, add a rationale, commit){" "}
          <b>→ Lakebase</b> (Engineer / Planner lens).
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 14 }}>
          {STEPS.map((s) => (
            <StepCard key={s.n} step={s} />
          ))}
        </div>

        {/* Why Lakebase */}
        <Divider />
        <SectionHead icon={<Database size={13} />}>Why Lakebase, not files or a vector-only store</SectionHead>
        <div style={grid}>
          {WHY.map((w) => (
            <div key={w.title} style={miniCard}>
              <div style={miniTitle}>{w.title}</div>
              <p style={miniBody}>{w.body}</p>
            </div>
          ))}
        </div>

        {/* Architecture */}
        <Divider />
        <SectionHead icon={<GitBranch size={13} />}>Architecture at a glance</SectionHead>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {ARCH.map((a) => (
            <div key={a.label} style={archRow}>
              <div style={archIcon}>{a.icon}</div>
              <div>
                <span style={{ fontWeight: 700, fontSize: 12.5, color: "var(--fg-1)" }}>{a.label}</span>
                <span style={{ fontSize: 12.5, color: "var(--fg-2)", lineHeight: 1.5 }}> — {a.body}</span>
              </div>
            </div>
          ))}
        </div>
        <div style={noteBox}>
          <b>Access note:</b> operational reads run as the app service principal today, so every user
          sees the same governed data. In production that would be Postgres row-level security keyed on
          the caller's identity — not an app-side ACL.
        </div>
      </div>
    </div>
  );
}

function StepCard({ step }: { step: Step }) {
  return (
    <div style={stepCard}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span style={stepNum}>{step.n}</span>
        <span style={stepIcon}>{step.icon}</span>
        <span style={{ fontWeight: 700, fontSize: 14, color: "var(--fg-1)", letterSpacing: "-0.01em" }}>
          {step.title}
        </span>
      </div>
      <Line label="Show" color="var(--db-blue-600)" text={step.show} />
      <Line label="Say" color="var(--db-maroon-500)" text={step.say} italic />
      <Line label="Outcome" color="var(--db-green-700)" text={step.outcome} last />
    </div>
  );
}

function Line({ label, color, text, italic, last }: { label: string; color: string; text: string; italic?: boolean; last?: boolean }) {
  return (
    <div style={{ display: "flex", gap: 10, marginBottom: last ? 0 : 8 }}>
      <span style={{ ...lineLabel, color }}>{label}</span>
      <span style={{ fontSize: 12.5, color: "var(--fg-2)", lineHeight: 1.5, fontStyle: italic ? "italic" : "normal" }}>
        {text}
      </span>
    </div>
  );
}

function SectionHead({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <h2 style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 0 12px", fontSize: 18, letterSpacing: "-0.02em" }}>
      <span style={{ color: "var(--db-lava-600)", display: "inline-flex" }}>{icon}</span>
      {children}
    </h2>
  );
}

function Divider() {
  return <div style={{ height: 1, background: "var(--border)", margin: "28px 0 20px" }} />;
}

// ── styles ───────────────────────────────────────────────────────────────────
const eyebrow: CSSProperties = {
  textTransform: "uppercase",
  letterSpacing: "var(--tracking-eyebrow)",
  fontWeight: 600,
  color: "var(--fg-2)",
};
const chip: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  fontWeight: 600,
  color: "var(--db-lava-700)",
  background: "var(--bg-subtle)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-pill)",
  padding: "4px 11px",
};
const prose: CSSProperties = {
  fontSize: 13,
  color: "var(--fg-2)",
  lineHeight: 1.6,
  margin: "0 0 12px",
  maxWidth: 720,
};
const stepCard: CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  background: "var(--bg-canvas)",
  padding: 16,
  boxShadow: "var(--shadow-sm)",
};
const stepNum: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 22,
  height: 22,
  borderRadius: 7,
  background: "var(--db-navy-800)",
  color: "var(--fg-on-dark)",
  fontFamily: "var(--font-mono)",
  fontSize: 12,
  fontWeight: 700,
};
const stepIcon: CSSProperties = { color: "var(--db-lava-600)", display: "inline-flex" };
const lineLabel: CSSProperties = {
  ...eyebrow,
  fontSize: 9,
  minWidth: 52,
  paddingTop: 2,
  flexShrink: 0,
};
const grid: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
  gap: 10,
};
const miniCard: CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-md)",
  background: "var(--bg-canvas)",
  padding: "12px 14px",
};
const miniTitle: CSSProperties = { fontWeight: 700, fontSize: 12.5, color: "var(--fg-1)", marginBottom: 5 };
const miniBody: CSSProperties = { fontSize: 12, color: "var(--fg-2)", lineHeight: 1.5, margin: 0 };
const archRow: CSSProperties = { display: "flex", gap: 11, alignItems: "flex-start" };
const archIcon: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 26,
  height: 26,
  borderRadius: 8,
  background: "var(--bg-subtle)",
  border: "1px solid var(--border)",
  color: "var(--db-lava-600)",
  flexShrink: 0,
};
const noteBox: CSSProperties = {
  marginTop: 16,
  padding: "11px 14px",
  background: "var(--bg-subtle)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-md)",
  fontSize: 12,
  color: "var(--fg-2)",
  lineHeight: 1.55,
};
