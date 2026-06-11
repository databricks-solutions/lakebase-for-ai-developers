import { useMemo, useState, type CSSProperties } from "react";
import {
  ArrowRight,
  Boxes,
  Brain,
  Check,
  Clock,
  Cpu,
  Database,
  FileSearch,
  Minus,
  Plus,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import type {
  ActionDecisionInput,
  ActionFact,
  AgentExtras,
  EvidenceBundle,
  PlannedAction,
  ResumeDecisions,
} from "../types";

// ── helpers ────────────────────────────────────────────────────────────────────────────────
const fmt = (n: number) => n.toLocaleString("en-US");
const usd = (n: number) =>
  n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : `$${Math.round(n / 1000)}K`;

const KIND_LABEL: Record<string, string> = {
  expedite_po: "Expedite",
  split_source: "New PO",
  raise_safety_stock: "Parameter",
  allocation_constraint: "Allocation",
  quality_hold: "Quality hold",
  quarantine_po: "Quarantine",
  tighten_inspection: "Inspection",
  supplier_quality_hold: "Supplier hold",
};

type LocalStatus = "approve" | "hold";
interface LocalDecision {
  status: LocalStatus;
  qty?: number;
  ssOverride?: number;
}

export interface ReviewPanelProps {
  /** The paused recommendation pulled from the active thread's last assistant message. */
  recommendation?: AgentExtras["recommendation"];
  evidence?: EvidenceBundle;
  /** Backend-built workspace deep-link to the run's trace (the bare /ml/traces/{id} path 404s). */
  traceUrl?: string | null;
  workspaceHost?: string;
  busy: boolean;
  /** Whether a plan is actually awaiting review (the message carried an approval_request). */
  hasPausedPlan: boolean;
  onResumeStructured: (decisions: ResumeDecisions) => void | Promise<void>;
  onGoToChat: () => void;
  onGoToLakebase: () => void;
  /** Set after a successful commit (the parent sets this when the resume `done` arrives). */
  committed: boolean;
}

export function ReviewPanel({
  recommendation,
  evidence,
  traceUrl,
  workspaceHost,
  busy,
  hasPausedPlan,
  onResumeStructured,
  onGoToChat,
  onGoToLakebase,
  committed,
}: ReviewPanelProps) {
  const actions: PlannedAction[] = recommendation?.planned_actions ?? [];

  // ── Empty state: no plan awaiting review ───────────────────────────────────────────────
  if (!hasPausedPlan || actions.length === 0) {
    return (
      <Page>
        <PageHead
          kicker="Human-in-the-loop"
          title="Review the plan. Your decisions commit to Lakebase."
        />
        <div style={emptyCard} data-tour="review-empty">
          <ShieldCheck size={22} color="var(--db-navy-400)" />
          <div>
            <p style={{ fontWeight: 600, margin: 0 }}>No plan awaiting review</p>
            <p style={{ color: "var(--fg-2)", margin: "4px 0 0", fontSize: "var(--fs-body-sm)" }}>
              Ask the planner in Chat — e.g. surface similar quality issues for Henkel SKU-1001
              (on-hand 40, open POs 800, coverage gap 760) and it will propose actions for your
              approval here.
            </p>
          </div>
          <button onClick={onGoToChat} style={primaryBtn(false)}>
            Go to Chat <ArrowRight size={15} />
          </button>
        </div>
      </Page>
    );
  }

  return (
    <ReviewBody
      // Include the values that seed ReviewBody's local state so a replan with the same action
      // keys but different qty/default_status/editability re-mounts and re-seeds (avoids
      // committing stale defaults).
      key={actions.map((a) => `${a.key}:${a.qty}:${a.default_status ?? ""}:${a.editable ? 1 : 0}`).join("|")}
      recommendation={recommendation}
      actions={actions}
      evidence={evidence}
      traceUrl={traceUrl}
      workspaceHost={workspaceHost}
      busy={busy}
      committed={committed}
      onResumeStructured={onResumeStructured}
      onGoToLakebase={onGoToLakebase}
    />
  );
}

function ReviewBody({
  recommendation,
  actions,
  evidence,
  traceUrl,
  workspaceHost,
  busy,
  committed,
  onResumeStructured,
  onGoToLakebase,
}: {
  recommendation?: AgentExtras["recommendation"];
  actions: PlannedAction[];
  evidence?: EvidenceBundle;
  traceUrl?: string | null;
  workspaceHost?: string;
  busy: boolean;
  committed: boolean;
  onResumeStructured: (decisions: ResumeDecisions) => void | Promise<void>;
  onGoToLakebase: () => void;
}) {
  // Per-action local state, initialized from default_status / qty.
  const [decisions, setDecisions] = useState<Record<string, LocalDecision>>(() => {
    const init: Record<string, LocalDecision> = {};
    for (const a of actions) {
      init[a.key] = {
        status: a.default_status ?? "approve",
        qty: a.editable ? a.qty : undefined,
      };
    }
    return init;
  });
  const [rationale, setRationale] = useState("");

  const setStatus = (key: string, status: LocalStatus) =>
    setDecisions((p) => ({ ...p, [key]: { ...p[key], status } }));
  const setQty = (key: string, qty: number) =>
    setDecisions((p) => ({ ...p, [key]: { ...p[key], qty } }));
  const setSs = (key: string, ssOverride: number) =>
    setDecisions((p) => ({ ...p, [key]: { ...p[key], ssOverride } }));

  const approvedCount = useMemo(
    () => Object.values(decisions).filter((d) => d.status === "approve").length,
    [decisions]
  );
  const decidedCount = actions.length; // every action always has a status (approve|hold)

  // Derived write-back ledger (approved actions only).
  const writes = useMemo(() => {
    const o: { key: string; op: string; table: string; summary: string }[] = [];
    for (const a of actions) {
      const d = decisions[a.key];
      if (!d || d.status !== "approve") continue;
      const op = a.target_table === "planning_parameters" ? "UPDATE" : "INSERT";
      const qty = d.qty ?? a.qty;
      const qtyStr = qty != null ? ` · ${fmt(qty)} u` : "";
      const ssStr =
        a.kind === "raise_safety_stock" && d.ssOverride != null ? ` → ${fmt(d.ssOverride)}` : "";
      o.push({
        key: a.key,
        op,
        table: a.target_table,
        summary: `${a.title}${qtyStr}${ssStr}`,
      });
    }
    return o;
  }, [actions, decisions]);

  const costDelta = useMemo(
    () =>
      actions.reduce((sum, a) => {
        const d = decisions[a.key];
        if (!d || d.status !== "approve") return sum;
        return sum + (a.cost_delta ?? 0);
      }, 0),
    [actions, decisions]
  );

  const canCommit =
    approvedCount > 0 && rationale.trim().length >= 12 && !busy && !committed;

  const commit = () => {
    if (!canCommit) return;
    const action_decisions: ActionDecisionInput[] = actions.map((a) => {
      const d = decisions[a.key];
      const out: ActionDecisionInput = { key: a.key, status: d?.status ?? "hold" };
      if (a.editable && d?.qty != null && d.qty !== a.qty) out.edited_qty = d.qty;
      if (a.kind === "raise_safety_stock" && d?.ssOverride != null)
        out.safety_stock_override = d.ssOverride;
      return out;
    });
    void onResumeStructured({ verdict: "approved", rationale: rationale.trim(), action_decisions });
  };

  const tUrl = traceUrl ?? null;

  return (
    <Page>
      <PageHead
        kicker="Human-in-the-loop"
        title="Review the plan. Your decisions commit to Lakebase."
        right={
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {tUrl && (
              <a href={tUrl} target="_blank" rel="noreferrer" style={traceLink}>
                Open in MLflow ↗
              </a>
            )}
            {committed ? <Pill tone="green">Committed</Pill> : <Pill tone="amber" dot>Awaiting</Pill>}
          </div>
        }
      />

      <Stepper committed={committed} />

      <div style={twoCol} data-tour="review-actions">
        <div>
          {recommendation?.summary && (
            <div style={summaryCard}>
              <p style={{ margin: 0, fontWeight: 500 }}>{recommendation.summary}</p>
            </div>
          )}

          {/* KPIs */}
          <div style={kpiRow}>
            <Stat label="On-hand" value="40 u" tone="var(--danger)" />
            <Stat label="Open POs" value="800 u" />
            <Stat label="Coverage gap" value="760 u" />
            <Stat
              label="Mitigation cost"
              value={costDelta ? usd(costDelta) : "—"}
              tone="var(--db-yellow-700)"
            />
          </div>

          {/* Evidence */}
          <SectionLabel icon={<Boxes size={13} />}>Gather · evidence</SectionLabel>
          <EvidenceGrid evidence={evidence} />

          {/* Actions */}
          <div style={{ display: "flex", alignItems: "center", margin: "20px 0 11px" }}>
            <SectionLabel icon={<Cpu size={13} />} inline>
              Proposed actions
            </SectionLabel>
            <span style={{ ...mono, fontSize: 11, color: "var(--fg-2)", marginLeft: "auto" }}>
              {approvedCount}/{decidedCount} approved
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 11 }}>
            {actions.map((a) => {
              const d = decisions[a.key] ?? { status: "approve" as LocalStatus };
              return (
                <ActionCard
                  key={a.key}
                  a={a}
                  status={d.status}
                  qty={d.qty}
                  disabled={busy || committed}
                  onStatus={(s) => setStatus(a.key, s)}
                  onQty={(q) => setQty(a.key, q)}
                />
              );
            })}
          </div>

          {/* Safety-stock slider — only for the approved raise_safety_stock action */}
          {actions.map((a) => {
            const d = decisions[a.key];
            if (a.kind !== "raise_safety_stock" || d?.status !== "approve") return null;
            const min = a.qty_min ?? 0;
            const max = a.qty_max ?? Math.max(min + 1, (a.qty ?? min) * 2);
            const step = a.qty_step ?? 100;
            const val = d.ssOverride ?? a.qty ?? min;
            return (
              <div key={`ss-${a.key}`} style={sliderCard}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ ...eyebrow, fontSize: 9 }}>Override safety stock · {a.sku ?? "SKU-1001"}</span>
                  <span style={{ ...mono, fontSize: 13, fontWeight: 600, color: "var(--db-yellow-700)" }}>
                    {fmt(val)} u
                  </span>
                </div>
                <input
                  type="range"
                  min={min}
                  max={max}
                  step={step}
                  value={val}
                  disabled={busy || committed}
                  onChange={(e) => setSs(a.key, +e.target.value)}
                  style={{ width: "100%", accentColor: "var(--db-yellow-600)" }}
                />
                <div style={{ ...mono, display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--fg-2)", marginTop: 4 }}>
                  <span>{fmt(min)} now</span>
                  <span>{fmt(max)} max</span>
                </div>
              </div>
            );
          })}

          {/* Rationale + commit */}
          <div style={commitCard(canCommit)} data-tour="review-commit">
            <label htmlFor="rat" style={{ ...eyebrow, fontSize: 10, display: "block", marginBottom: 4 }}>
              Decision rationale <span style={{ color: "var(--danger)" }}>· required</span>
            </label>
            <p style={{ fontSize: 11, color: "var(--fg-2)", margin: "0 0 8px" }}>
              Saved alongside the decision and embedded into agent memory for future recall.
            </p>
            <textarea
              id="rat"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              disabled={busy || committed}
              placeholder="e.g. Mirror the prior SKU-1001 hold — quarantine the failing lot + incoming PO, tighten inspection, hold SUP-001 until adhesion/thermal validation passes."
              style={rationaleArea(committed)}
            />
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12, flexWrap: "wrap" }}>
              {!committed ? (
                <button onClick={commit} disabled={!canCommit} style={primaryBtn(!canCommit)}>
                  {busy ? (
                    <>
                      <Spinner /> Committing…
                    </>
                  ) : (
                    <>
                      <Database size={15} /> Commit {writes.length} write{writes.length !== 1 ? "s" : ""}
                    </>
                  )}
                </button>
              ) : (
                <>
                  <span style={{ display: "flex", alignItems: "center", gap: 7, color: "var(--success)", fontWeight: 600 }}>
                    <Sparkles size={15} /> Committed to Lakebase
                  </span>
                  <button onClick={onGoToLakebase} style={ghostLinkBtn}>
                    See it in Lakebase <ArrowRight size={13} />
                  </button>
                </>
              )}
              {!committed && (
                <span style={{ fontSize: 11, color: "var(--fg-2)", display: "flex", alignItems: "center", gap: 6 }}>
                  {rationale.trim().length >= 12 ? (
                    <>
                      <Check size={13} color="var(--success)" /> Rationale captured
                    </>
                  ) : (
                    <>
                      <Clock size={13} /> Add a rationale (≥ 12 chars) to commit
                    </>
                  )}
                </span>
              )}
            </div>
          </div>
        </div>

        <Ledger writes={writes} committed={committed} committing={busy && !committed} />
      </div>
    </Page>
  );
}

// ── Evidence ───────────────────────────────────────────────────────────────────────────────
function EvidenceGrid({ evidence }: { evidence?: EvidenceBundle }) {
  const dataRows = evidence?.data ?? [];
  const rag = evidence?.rag ?? [];
  const memory = evidence?.memory ?? [];
  const anyEvidence = dataRows.length || rag.length || memory.length;

  return (
    <div style={evidenceGrid}>
      <EvidenceCard
        tag="Data agent"
        via={dataRows.length ? `${dataRows.length} rows` : "operational"}
        icon={<Database size={13} />}
        accent="var(--db-blue-600)"
        soft="#e4ebfb"
        head={dataRows.length ? "Operational rows joined" : "Coverage gap on SKU-1001"}
        body={
          dataRows.length
            ? "Similarity + on-hand + open POs resolved in one governed Lakebase query."
            : "On-hand 40 against open POs 800 leaves a 760-unit coverage gap on the line."
        }
        chips={
          dataRows.length
            ? summarizeRow(dataRows[0])
            : ["on-hand 40", "open POs 800", "gap 760"]
        }
      />
      <EvidenceCard
        tag="RAG agent"
        via={rag.length ? `${rag.length} sources` : "knowledge"}
        icon={<FileSearch size={13} />}
        accent="var(--db-yellow-700)"
        soft="#fbefd8"
        head={rag.length ? (rag[0].source ?? "Supplier context") : "Supplier quality risk on file"}
        body={
          rag.length
            ? truncate(rag[0].content ?? "", 160)
            : "Knowledge corpus surfaced the Henkel cracking incident and the contract terms governing a quality hold."
        }
        chips={rag.length ? rag.slice(0, 3).map((r) => r.source ?? "source") : ["Henkel note", "MSA terms", "DuPont alt"]}
      />
      <EvidenceCard
        tag="Memory agent"
        via={memory.length && memory[0].score != null ? `recall · ${memory[0].score?.toFixed(2)}` : "recalled"}
        icon={<Brain size={13} />}
        accent="var(--db-green-700)"
        soft="#dbf2ec"
        head={memory.length ? "Prior decision retrieved" : "Recalled precedent"}
        body={
          memory.length
            ? truncate(memory[0].text, 160)
            : "A near-identical prior adhesive quality hold and its recalled resolution ground this proposal."
        }
        chips={
          memory.length
            ? memory.slice(0, 3).map((m) => (m.score != null ? `sim ${m.score.toFixed(2)}` : "recalled"))
            : ["prior decision", "recalled", "adhesive"]
        }
      />
      {!anyEvidence && (
        <p style={{ gridColumn: "1 / -1", color: "var(--fg-3)", fontSize: 11, margin: 0 }}>
          Evidence shown is illustrative until the gather agents stream their bundle.
        </p>
      )}
    </div>
  );
}

function summarizeRow(row: Record<string, unknown>): string[] {
  return Object.entries(row)
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${String(v)}`);
}
function truncate(s: string | undefined | null, n: number): string {
  // Defensive: evidence fields can be absent/non-string depending on the gather path; never throw
  // (a render exception white-screens the whole app — there is no error boundary).
  const str = s == null ? "" : String(s);
  return str.length > n ? `${str.slice(0, n - 1)}…` : str;
}

function EvidenceCard({
  tag,
  via,
  icon,
  accent,
  soft,
  head,
  body,
  chips,
}: {
  tag: string;
  via: string;
  icon: React.ReactNode;
  accent: string;
  soft: string;
  head: string;
  body: string;
  chips: string[];
}) {
  return (
    <div style={evidenceCard}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 9 }}>
        <span style={{ display: "grid", placeItems: "center", width: 24, height: 24, borderRadius: 6, background: soft, color: accent }}>
          {icon}
        </span>
        <span style={{ ...eyebrow, fontSize: 9.5, color: "var(--fg-1)" }}>{tag}</span>
        <span style={{ ...mono, fontSize: 10, color: "var(--fg-2)", marginLeft: "auto" }}>{via}</span>
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 5 }}>{head}</div>
      <div style={{ fontSize: 11.5, color: "var(--fg-2)", lineHeight: 1.5, marginBottom: 10 }}>{body}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: "auto" }}>
        {chips.map((c, i) => (
          <span key={`${c}-${i}`} style={{ ...mono, fontSize: 9.5, color: accent, background: soft, padding: "2px 7px", borderRadius: 5 }}>
            {c}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Action card ────────────────────────────────────────────────────────────────────────────
function ActionCard({
  a,
  status,
  qty,
  disabled,
  onStatus,
  onQty,
}: {
  a: PlannedAction;
  status: LocalStatus;
  qty?: number;
  disabled: boolean;
  onStatus: (s: LocalStatus) => void;
  onQty: (q: number) => void;
}) {
  const approved = status === "approve";
  const held = status === "hold";
  const railColor = approved ? "var(--db-green-600)" : "var(--db-navy-300)";
  const effectiveQty = qty ?? a.qty ?? 0;
  const edited = a.editable && a.qty != null && effectiveQty !== a.qty;
  const min = a.qty_min ?? 0;
  const step = a.qty_step ?? 500;

  return (
    <div style={actionCard(approved, held)}>
      <span style={{ position: "absolute", left: 0, top: 12, bottom: 12, width: 4, borderRadius: 4, background: railColor }} />
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
            <span style={kindBadge(approved, held)}>{KIND_LABEL[a.kind] ?? a.kind}</span>
            <span style={{ fontSize: 14, fontWeight: 600 }}>{a.title}</span>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--fg-2)", lineHeight: 1.5 }}>{a.detail}</div>
          <div style={{ display: "flex", gap: 18, marginTop: 11, flexWrap: "wrap" }}>
            {a.editable ? (
              <div>
                <div style={{ ...eyebrow, fontSize: 9, marginBottom: 5 }}>{a.qty_label ?? "Units"}</div>
                <QtyStepper value={effectiveQty} onChange={onQty} step={step} min={min} disabled={disabled} />
                {edited && (
                  <span style={{ ...mono, fontSize: 9.5, color: "var(--db-yellow-700)", marginLeft: 8 }}>
                    was {fmt(a.qty ?? 0)}
                  </span>
                )}
              </div>
            ) : (
              (a.facts ?? []).map((f: ActionFact) => (
                <div key={f.k}>
                  <div style={{ ...eyebrow, fontSize: 9, marginBottom: 3 }}>{f.k}</div>
                  <div style={{ ...mono, fontSize: 12.5, fontWeight: 600, color: f.tone ?? "var(--fg-1)" }}>{f.v}</div>
                </div>
              ))
            )}
            <div>
              <div style={{ ...eyebrow, fontSize: 9, marginBottom: 3 }}>Writes to</div>
              <div style={{ ...mono, fontSize: 12, fontWeight: 600, color: "var(--db-blue-600)" }}>{a.target_table}</div>
            </div>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <button onClick={() => onStatus("approve")} disabled={disabled} style={decisionBtn(approved, "approve")}>
            <Check size={13} /> {approved ? "Approved" : "Approve"}
          </button>
          <button onClick={() => onStatus("hold")} disabled={disabled} style={decisionBtn(held, "hold")}>
            <X size={13} /> {held ? "Held" : "Hold"}
          </button>
        </div>
      </div>
    </div>
  );
}

function QtyStepper({
  value,
  onChange,
  step,
  min,
  disabled,
}: {
  value: number;
  onChange: (v: number) => void;
  step: number;
  min: number;
  disabled: boolean;
}) {
  return (
    <div style={qtyStepper}>
      <button
        onClick={() => onChange(Math.max(min, value - step))}
        disabled={disabled}
        style={stepBtn}
        aria-label="decrease"
      >
        <Minus size={13} />
      </button>
      <span style={{ ...mono, fontSize: 12.5, fontWeight: 600, minWidth: 60, textAlign: "center" }}>{fmt(value)}</span>
      <button onClick={() => onChange(value + step)} disabled={disabled} style={stepBtn} aria-label="increase">
        <Plus size={13} />
      </button>
    </div>
  );
}

// ── Ledger ─────────────────────────────────────────────────────────────────────────────────
function Ledger({
  writes,
  committed,
  committing,
}: {
  writes: { key: string; op: string; table: string; summary: string }[];
  committed: boolean;
  committing: boolean;
}) {
  return (
    <aside style={ledgerCard} data-tour="review-ledger">
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
        <Database size={15} color="var(--db-green-500)" />
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--fg-on-dark)" }}>Lakebase</span>
        <span style={{ ...mono, fontSize: 9.5, color: "var(--fg-on-dark-2)", marginLeft: "auto" }}>scp_planning</span>
      </div>
      <div style={{ fontSize: 10.5, color: "var(--fg-on-dark-2)", marginBottom: 14 }}>Write-back ledger</div>
      {writes.length === 0 ? (
        <div style={ledgerEmpty}>Approve an action to stage a row.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          {writes.map((w) => (
            <div key={w.key} style={ledgerRow(committed)}>
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: 9,
                  background: committed ? "var(--db-green-500)" : "var(--db-yellow-600)",
                  flexShrink: 0,
                  animation: committed ? undefined : "cursor-blink 1.6s ease-in-out infinite",
                }}
              />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ ...mono, fontSize: 10.5, color: "var(--fg-on-dark)", fontWeight: 600 }}>
                  {w.op} {w.table}
                </div>
                <div style={{ ...mono, fontSize: 9.5, color: "var(--fg-on-dark-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {w.summary}
                </div>
              </div>
              {committed ? (
                <Check size={13} color="var(--db-green-500)" />
              ) : (
                <span style={{ ...eyebrow, fontSize: 8, color: "var(--db-yellow-600)" }}>staged</span>
              )}
            </div>
          ))}
        </div>
      )}
      <div style={memoryNote(committed)}>
        <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 6 }}>
          <Brain size={13} color={committed ? "var(--db-green-500)" : "var(--fg-on-dark-2)"} />
          <span style={{ ...eyebrow, fontSize: 9, color: committed ? "var(--db-green-500)" : "var(--fg-on-dark-2)" }}>
            agent_memory · pgvector
          </span>
        </div>
        <div style={{ fontSize: 10.8, color: committed ? "var(--fg-on-dark)" : "var(--fg-on-dark-2)", lineHeight: 1.5 }}>
          {committed
            ? "Decision embedded — retrievable on the next similar quality issue."
            : "On commit, this decision is embedded for future recall."}
        </div>
      </div>
      {committing && (
        <div style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 8, color: "var(--db-yellow-600)", fontSize: 11 }}>
          <Spinner color="var(--db-yellow-600)" />
          <span style={mono}>BEGIN; flushing…</span>
        </div>
      )}
    </aside>
  );
}

// ── Stepper ────────────────────────────────────────────────────────────────────────────────
const STEP = ["Supervisor", "Gather", "Planner", "Human review", "Commit"];
function Stepper({ committed }: { committed: boolean }) {
  return (
    <div style={{ display: "flex", alignItems: "center", overflowX: "auto", marginBottom: 16 }}>
      {STEP.map((label, i) => {
        const done = committed ? true : i < 3;
        const active = committed ? i === 4 : i === 3;
        return (
          <span key={label} style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
            <span style={stepPill(active)}>
              <span style={stepDot(active, done)}>
                {done && !active ? <Check size={12} /> : <span style={{ ...mono, fontSize: 10, fontWeight: 700 }}>{i + 1}</span>}
              </span>
              <span style={{ fontSize: 11.5, fontWeight: 600, color: active ? "var(--db-yellow-700)" : done ? "var(--fg-1)" : "var(--fg-2)" }}>
                {label}
              </span>
            </span>
            {i < STEP.length - 1 && <ArrowRight size={13} color="var(--db-navy-300)" style={{ flexShrink: 0, margin: "0 2px" }} />}
          </span>
        );
      })}
    </div>
  );
}

// ── Shared atoms ───────────────────────────────────────────────────────────────────────────
function Page({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ animation: "fade-up var(--dur-base) var(--ease-out)" }}>
      <div style={{ maxWidth: 1040, margin: "0 auto", padding: "var(--space-5) var(--space-5) var(--space-7)" }}>
        {children}
      </div>
    </div>
  );
}

function PageHead({ kicker, title, sub, right }: { kicker: string; title: string; sub?: string; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 14, marginBottom: 18, flexWrap: "wrap" }}>
      <div>
        <div style={{ ...eyebrow, fontSize: 9.5, marginBottom: 4 }}>{kicker}</div>
        <h2 style={{ margin: 0, fontSize: 22, letterSpacing: "-0.02em", lineHeight: 1.1 }}>{title}</h2>
        {sub && <p style={{ fontSize: 12.5, color: "var(--fg-2)", margin: "6px 0 0", maxWidth: 560, lineHeight: 1.5 }}>{sub}</p>}
      </div>
      {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
    </div>
  );
}

function SectionLabel({ icon, inline, children }: { icon: React.ReactNode; inline?: boolean; children: React.ReactNode }) {
  return (
    <span
      style={{
        ...eyebrow,
        fontSize: 9.5,
        display: inline ? "inline-flex" : "flex",
        alignItems: "center",
        gap: 7,
        marginBottom: inline ? 0 : 10,
      }}
    >
      {icon} {children}
    </span>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ flex: 1, minWidth: 80 }}>
      <div style={{ ...eyebrow, fontSize: 9, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 600, color: tone ?? "var(--fg-1)", letterSpacing: "-0.01em" }}>{value}</div>
    </div>
  );
}

function Pill({ tone, dot, children }: { tone: "amber" | "green"; dot?: boolean; children: React.ReactNode }) {
  const c =
    tone === "green"
      ? { bg: "#dbf2ec", fg: "var(--db-green-700)", line: "#a9e0d4" }
      : { bg: "#fbefd8", fg: "var(--db-yellow-700)", line: "#f0d49a" };
  return (
    <span style={{ ...eyebrow, display: "inline-flex", alignItems: "center", gap: 6, fontSize: 10, padding: "4px 9px", borderRadius: 999, background: c.bg, color: c.fg, border: `1px solid ${c.line}`, whiteSpace: "nowrap" }}>
      {dot && <span style={{ width: 6, height: 6, borderRadius: 9, background: c.fg, animation: "cursor-blink 1.6s ease-in-out infinite" }} />}
      {children}
    </span>
  );
}

function Spinner({ color = "#fff" }: { color?: string }) {
  return (
    <span
      style={{
        width: 14,
        height: 14,
        border: `2px solid ${color}`,
        borderTopColor: "transparent",
        borderRadius: 9,
        display: "inline-block",
        animation: "spin 0.9s linear infinite",
      }}
    />
  );
}

// ── Inline styles ──────────────────────────────────────────────────────────────────────────
const eyebrow: CSSProperties = {
  textTransform: "uppercase",
  letterSpacing: "var(--tracking-eyebrow)",
  fontWeight: 600,
  color: "var(--fg-2)",
};
const mono: CSSProperties = { fontFamily: "var(--font-mono)" };

const twoCol: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) 290px",
  gap: 16,
  alignItems: "start",
};
const kpiRow: CSSProperties = {
  display: "flex",
  gap: 20,
  padding: 16,
  background: "var(--bg-canvas)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  marginBottom: 16,
  flexWrap: "wrap",
};
const summaryCard: CSSProperties = {
  background: "var(--bg-canvas)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "var(--space-4)",
  marginBottom: 16,
};
const evidenceGrid: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
  gap: 12,
};
const evidenceCard: CSSProperties = {
  background: "var(--bg-canvas)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: 14,
  display: "flex",
  flexDirection: "column",
};
const sliderCard: CSSProperties = {
  background: "var(--bg-canvas)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: 14,
  marginTop: 11,
};
const emptyCard: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 16,
  background: "var(--bg-canvas)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  padding: "var(--space-5)",
  boxShadow: "var(--shadow-sm)",
  flexWrap: "wrap",
};

function commitCard(active: boolean): CSSProperties {
  return {
    background: "var(--bg-canvas)",
    border: `1px solid ${active ? "var(--db-yellow-600)" : "var(--border)"}`,
    borderRadius: "var(--radius-lg)",
    padding: 16,
    marginTop: 18,
  };
}
function rationaleArea(committed: boolean): CSSProperties {
  return {
    width: "100%",
    minHeight: 60,
    resize: "vertical",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-md)",
    padding: "9px 11px",
    fontSize: 12.5,
    font: "inherit",
    lineHeight: 1.5,
    background: committed ? "var(--bg-subtle)" : "var(--bg-canvas)",
    color: "var(--fg-1)",
  };
}
function actionCard(approved: boolean, held: boolean): CSSProperties {
  return {
    position: "relative",
    background: "var(--bg-canvas)",
    border: `1px solid ${approved ? "#a9e0d4" : held ? "var(--border)" : "#f0d49a"}`,
    borderRadius: "var(--radius-lg)",
    padding: "14px 16px 14px 18px",
    opacity: held ? 0.7 : 1,
  };
}
function kindBadge(approved: boolean, held: boolean): CSSProperties {
  return {
    ...eyebrow,
    fontSize: 8.5,
    color: approved ? "var(--db-green-700)" : held ? "var(--fg-2)" : "var(--db-yellow-700)",
    background: approved ? "#dbf2ec" : held ? "var(--bg-subtle)" : "#fbefd8",
    padding: "2px 7px",
    borderRadius: 5,
  };
}
function decisionBtn(active: boolean, kind: "approve" | "hold"): CSSProperties {
  const approveActive = active && kind === "approve";
  const holdActive = active && kind === "hold";
  return {
    display: "flex",
    alignItems: "center",
    gap: 6,
    justifyContent: "center",
    font: "inherit",
    fontSize: 11.5,
    fontWeight: 600,
    padding: "7px 12px",
    borderRadius: "var(--radius-md)",
    cursor: "pointer",
    border: `1px solid ${approveActive ? "var(--db-green-600)" : holdActive ? "var(--db-lava-600)" : "var(--border)"}`,
    background: approveActive ? "var(--db-green-600)" : holdActive ? "#fbe3e5" : "var(--bg-canvas)",
    color: approveActive ? "#fff" : holdActive ? "var(--db-lava-700)" : "var(--fg-2)",
  };
}
const qtyStepper: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-md)",
  overflow: "hidden",
  background: "var(--bg-subtle)",
};
const stepBtn: CSSProperties = {
  border: "none",
  background: "transparent",
  padding: "6px 8px",
  color: "var(--fg-2)",
  cursor: "pointer",
};

function primaryBtn(disabled: boolean): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    font: "inherit",
    fontWeight: 600,
    fontSize: 13,
    padding: "11px 18px",
    borderRadius: "var(--radius-md)",
    border: "none",
    background: disabled ? "var(--db-navy-300)" : "var(--db-navy-800)",
    color: disabled ? "#fff" : "var(--fg-on-dark)",
    cursor: disabled ? "not-allowed" : "pointer",
  };
}
const ghostLinkBtn: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  font: "inherit",
  fontSize: 11.5,
  fontWeight: 600,
  padding: "8px 13px",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--border)",
  background: "var(--bg-canvas)",
  color: "var(--db-blue-600)",
  cursor: "pointer",
};
const traceLink: CSSProperties = {
  fontSize: "var(--fs-body-sm)",
  whiteSpace: "nowrap",
};

const ledgerCard: CSSProperties = {
  background: "var(--bg-inverse)",
  color: "var(--fg-on-dark)",
  padding: 16,
  borderRadius: "var(--radius-lg)",
  alignSelf: "start",
};
const ledgerEmpty: CSSProperties = {
  padding: "18px 12px",
  textAlign: "center",
  border: "1px dashed var(--db-navy-600)",
  borderRadius: "var(--radius-md)",
  color: "var(--fg-on-dark-2)",
  fontSize: 11,
};
function ledgerRow(committed: boolean): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "9px 11px",
    borderRadius: "var(--radius-md)",
    background: "var(--db-navy-700)",
    border: `1px solid ${committed ? "#1f7a66" : "var(--db-navy-600)"}`,
  };
}
function memoryNote(committed: boolean): CSSProperties {
  return {
    marginTop: 14,
    padding: 12,
    borderRadius: "var(--radius-md)",
    background: committed ? "rgba(0,169,114,0.14)" : "var(--db-navy-700)",
    border: `1px solid ${committed ? "#1f7a66" : "var(--db-navy-600)"}`,
  };
}

function stepPill(active: boolean): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 7,
    padding: "6px 11px",
    borderRadius: "var(--radius-md)",
    background: active ? "#fbefd8" : "transparent",
    flexShrink: 0,
  };
}
function stepDot(active: boolean, done: boolean): CSSProperties {
  return {
    display: "grid",
    placeItems: "center",
    width: 20,
    height: 20,
    borderRadius: 6,
    background: active ? "var(--db-yellow-600)" : done ? "var(--db-green-600)" : "var(--db-navy-300)",
    color: "#fff",
  };
}
