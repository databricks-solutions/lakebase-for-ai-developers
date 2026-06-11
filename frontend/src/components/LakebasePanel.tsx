import { useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  Boxes,
  Brain,
  Check,
  Clock,
  Code2,
  Database,
  Lock,
  User,
} from "lucide-react";
import { getStateTables } from "../api";
import type { RecalledMemory, StateTableRow, StateTables } from "../types";

type Lens = "eng" | "plan";
type TableKey = "planning_parameters" | "approved_actions" | "constraints";

const TABLE_META: Record<TableKey, { plain: string; icon: React.ReactNode }> = {
  planning_parameters: { plain: "Active overrides", icon: <Boxes size={13} /> },
  approved_actions: { plain: "Decisions on record", icon: <Check size={13} /> },
  constraints: { plain: "Rules the agent follows", icon: <Lock size={13} /> },
};

// Plain-language labels for known columns; unknown columns fall back to the raw name.
const COL_LABELS: Record<string, string> = {
  param_id: "ID",
  action_id: "ID",
  constraint_id: "ID",
  sku: "Part",
  parameter: "Setting",
  prev_value: "Was",
  new_value: "Now",
  expires_on: "Until",
  approved_by: "By",
  created_by: "By",
  type: "Action",
  reference: "Ref",
  qty: "Qty",
  cost_delta: "Cost Δ",
  status: "Status",
  rule: "Rule",
  scope: "Applies to",
  active: "On",
};
const NUMERIC_COLS = new Set(["prev_value", "new_value", "qty", "cost_delta"]);
const HIDE_IN_PLAN = new Set(["param_id", "action_id", "constraint_id"]);
const colType = (k: string): string => {
  if (k.endsWith("_id")) return "uuid";
  if (k === "active") return "bool";
  if (k.endsWith("_on") || k === "expires_on") return "date";
  if (NUMERIC_COLS.has(k)) return "numeric";
  return "text";
};

export interface LakebasePanelProps {
  thread: string;
  workspaceHost?: string;
  /** Bump to force a re-fetch (e.g. after a commit on Review). */
  refreshKey?: number;
}

export function LakebasePanel({ thread, refreshKey = 0 }: LakebasePanelProps) {
  const [lens, setLens] = useState<Lens>("eng");
  const [tab, setTab] = useState<TableKey>("planning_parameters");
  const [data, setData] = useState<StateTables | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    getStateTables(thread)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setErr(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [thread, refreshKey]);

  const tables: Record<TableKey, StateTableRow[]> = useMemo(
    () => ({
      planning_parameters: data?.planning_parameters ?? [],
      approved_actions: data?.approved_actions ?? [],
      constraints: data?.constraints ?? [],
    }),
    [data]
  );
  const memory: RecalledMemory[] = data?.recalled_memory ?? [];
  const isEmpty =
    !loading &&
    !tables.planning_parameters.length &&
    !tables.approved_actions.length &&
    !tables.constraints.length;

  const rows = tables[tab];
  const cols = useMemo(() => {
    const keys = rows.length ? Object.keys(rows[0]) : [];
    return keys.filter((k) => !(lens === "plan" && HIDE_IN_PLAN.has(k)));
  }, [rows, lens]);

  return (
    <div style={{ animation: "fade-up var(--dur-base) var(--ease-out)" }}>
      <div style={{ maxWidth: 1040, margin: "0 auto", padding: "var(--space-5) var(--space-5) var(--space-7)" }}>
        <PageHead
          kicker="Lakebase state"
          title="What persists between runs"
          sub="The same rows, two lenses. Toggle to read them as raw Postgres or in plain language."
          right={<LensToggle lens={lens} setLens={setLens} />}
        />

        {err && (
          <div style={{ ...banner("amber"), marginBottom: 16 }}>
            <Clock size={15} color="var(--db-yellow-700)" /> Couldn't read Lakebase state: {err}
          </div>
        )}
        {isEmpty && !err && (
          <div style={{ ...banner("amber"), marginBottom: 16 }} data-tour="lakebase-empty">
            <Clock size={15} color="var(--db-yellow-700)" /> Nothing committed yet — new rows
            (the Henkel SKU-1001 mitigation: on-hand 40, open POs 800, gap 760) appear here once
            the planner commits on the&nbsp;<b>Review</b>&nbsp;page.
          </div>
        )}

        {/* Long-term tables */}
        <SectionLabel color="var(--db-green-700)" icon={<Database size={13} />}>
          Long-term · durable + vector
        </SectionLabel>
        <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
          {(Object.keys(TABLE_META) as TableKey[]).map((k) => {
            const on = tab === k;
            return (
              <button key={k} onClick={() => setTab(k)} style={tabBtn(on)}>
                {TABLE_META[k].icon}
                <span style={lens === "eng" ? { fontFamily: "var(--font-mono)", fontSize: 11 } : { fontSize: 12 }}>
                  {lens === "eng" ? k : TABLE_META[k].plain}
                </span>
              </button>
            );
          })}
        </div>

        <div style={tableWrap} data-tour="lakebase-tables">
          {loading ? (
            <div style={emptyRowMsg}>Loading…</div>
          ) : rows.length === 0 ? (
            <div style={emptyRowMsg}>No rows yet — commit on Review to write decisions here.</div>
          ) : (
            <table style={{ borderCollapse: "collapse", width: "100%" }}>
              <thead>
                <tr style={{ background: "var(--bg-subtle)" }}>
                  {cols.map((c) => (
                    <th key={c} style={th}>
                      <div style={{ ...eyebrow, fontSize: 9 }}>{lens === "eng" ? c : COL_LABELS[c] ?? c}</div>
                      {lens === "eng" && <TypeTag t={colType(c)} />}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} style={{ background: "var(--bg-canvas)" }}>
                    {cols.map((c) => (
                      <td key={c} style={{ ...td, borderBottom: i < rows.length - 1 ? "1px solid var(--border)" : "none" }}>
                        <Cell colKey={c} value={r[c]} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Agent memory / recalled decisions */}
        <div style={memoryCard(lens)}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <Brain size={15} color="var(--db-maroon-500)" />
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600, color: lens === "eng" ? "var(--fg-on-dark)" : "var(--fg-1)" }}>
              {lens === "eng" ? "agent_memory" : "What the assistant remembers"}
            </span>
            {lens === "eng" && (
              <span style={{ ...eyebrow, fontSize: 8.5, color: "var(--db-maroon-500)", background: "#fbe5f1", padding: "2px 6px", borderRadius: 5 }}>
                vector(1024) · hnsw
              </span>
            )}
          </div>
          {lens === "eng" && (
            <pre style={sqlPre}>{`SELECT decision_id, summary,
       1 - (embedding <=> :q) AS similarity
FROM agent_memory
ORDER BY embedding <=> :q LIMIT 3;`}</pre>
          )}
          {memory.length === 0 ? (
            <p style={{ fontSize: 11.5, color: lens === "eng" ? "var(--fg-on-dark-2)" : "var(--fg-2)", margin: 0 }}>
              No decisions recalled for this thread yet. Once the planner commits, the decision is
              embedded and surfaces here on the next similar shortage.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              {memory.map((m, i) => (
                <MemoryRow key={i} m={m} lens={lens} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MemoryRow({ m, lens }: { m: RecalledMemory; lens: Lens }) {
  const score = m.score ?? null;
  return (
    <div style={memoryRow(lens)}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5, flexWrap: "wrap" }}>
        {lens === "eng" && m.namespace && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--db-blue-600)" }}>{m.namespace}</span>
        )}
        {score != null ? (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10.5,
              fontWeight: 700,
              color: score >= 0.9 ? "var(--db-green-700)" : "var(--db-yellow-700)",
            }}
          >
            {lens === "eng" ? `sim ${score.toFixed(2)}` : `${Math.round(score * 100)}% match`}
          </span>
        ) : (
          <span style={{ ...eyebrow, fontSize: 8, color: "var(--db-green-700)", background: lens === "eng" ? "rgba(0,169,114,0.16)" : "#fff", border: "1px solid #a9e0d4", padding: "2px 7px", borderRadius: 5 }}>
            {lens === "eng" ? "written this run" : "saved today"}
          </span>
        )}
      </div>
      <div style={{ fontSize: 11.5, color: lens === "eng" ? "var(--fg-on-dark)" : "var(--fg-1)", lineHeight: 1.45 }}>
        {m.text}
      </div>
    </div>
  );
}

// ── Cell rendering ─────────────────────────────────────────────────────────────────────────
function Cell({ colKey, value }: { colKey: string; value: unknown }) {
  if (colKey === "active" || typeof value === "boolean") {
    const v = Boolean(value);
    return (
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10.5,
          fontWeight: 600,
          color: v ? "var(--db-green-700)" : "var(--fg-2)",
          background: v ? "#dbf2ec" : "var(--bg-subtle)",
          padding: "1px 7px",
          borderRadius: 5,
        }}
      >
        {String(v)}
      </span>
    );
  }
  const isId = colKey.endsWith("_id");
  const t = colType(colKey);
  const monoish = isId || ["numeric", "int", "date"].includes(t);
  const emphasized = colKey === "new_value" || colKey === "cost_delta";
  return (
    <span
      style={{
        fontFamily: monoish ? "var(--font-mono)" : "var(--font-sans)",
        fontSize: isId ? 10.5 : 11.5,
        fontWeight: emphasized ? 700 : 500,
        color: emphasized ? "var(--db-yellow-700)" : isId ? "var(--db-blue-600)" : "var(--fg-1)",
      }}
    >
      {value == null ? "—" : String(value)}
    </span>
  );
}

function TypeTag({ t }: { t: string }) {
  const map: Record<string, string> = {
    uuid: "var(--db-blue-600)",
    text: "var(--fg-2)",
    int: "var(--db-green-700)",
    numeric: "var(--db-green-700)",
    bool: "var(--db-yellow-700)",
    date: "var(--db-maroon-500)",
  };
  return <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: map[t] ?? "var(--fg-2)" }}>{t}</span>;
}

function LensToggle({ lens, setLens }: { lens: Lens; setLens: (l: Lens) => void }) {
  const opt = (k: Lens, icon: React.ReactNode, label: string) => {
    const on = lens === k;
    return (
      <button
        onClick={() => setLens(k)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          border: "none",
          borderRadius: "var(--radius-sm)",
          padding: "6px 12px",
          font: "inherit",
          fontSize: 11.5,
          fontWeight: 600,
          cursor: "pointer",
          background: on ? (k === "eng" ? "var(--db-navy-600)" : "var(--bg-canvas)") : "transparent",
          color: on ? (k === "eng" ? "var(--fg-on-dark)" : "var(--fg-1)") : "var(--fg-on-dark-2)",
          boxShadow: on && k === "plan" ? "var(--shadow-sm)" : "none",
        }}
      >
        {icon} {label}
      </button>
    );
  };
  return (
    <div data-tour="lakebase-lens" style={{ display: "inline-flex", gap: 3, padding: 3, borderRadius: "var(--radius-md)", background: "var(--db-navy-700)", border: "1px solid var(--db-navy-600)" }}>
      {opt("eng", <Code2 size={13} />, "Engineer")}
      {opt("plan", <User size={13} />, "Planner")}
    </div>
  );
}

// ── Shared atoms ───────────────────────────────────────────────────────────────────────────
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

function SectionLabel({ icon, color, children }: { icon: React.ReactNode; color?: string; children: React.ReactNode }) {
  return (
    <span style={{ ...eyebrow, fontSize: 9.5, color: color ?? "var(--fg-2)", display: "flex", alignItems: "center", gap: 7, marginBottom: 10 }}>
      {icon} {children}
    </span>
  );
}

// ── Inline styles ──────────────────────────────────────────────────────────────────────────
const eyebrow: CSSProperties = {
  textTransform: "uppercase",
  letterSpacing: "var(--tracking-eyebrow)",
  fontWeight: 600,
  color: "var(--fg-2)",
};
function banner(tone: "amber"): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 9,
    padding: "11px 14px",
    background: "#fbefd8",
    border: "1px solid #f0d49a",
    borderRadius: "var(--radius-lg)",
    fontSize: 12,
    color: "var(--db-yellow-700)",
  };
}
function tabBtn(on: boolean): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 7,
    border: `1px solid ${on ? "var(--db-navy-800)" : "var(--border)"}`,
    background: on ? "var(--db-navy-800)" : "var(--bg-canvas)",
    color: on ? "var(--fg-on-dark)" : "var(--fg-2)",
    borderRadius: "var(--radius-md)",
    padding: "8px 13px",
    font: "inherit",
    fontWeight: 600,
    cursor: "pointer",
  };
}
const tableWrap: CSSProperties = {
  overflowX: "auto",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-lg)",
  marginBottom: 16,
  background: "var(--bg-canvas)",
};
const th: CSSProperties = {
  padding: "9px 14px",
  borderBottom: "1px solid var(--border)",
  textAlign: "left",
  whiteSpace: "nowrap",
};
const td: CSSProperties = { padding: "10px 14px", textAlign: "left", whiteSpace: "nowrap" };
const emptyRowMsg: CSSProperties = { padding: "26px 16px", textAlign: "center", color: "var(--fg-2)", fontSize: 12 };

function memoryCard(lens: Lens): CSSProperties {
  return {
    background: lens === "eng" ? "var(--bg-inverse)" : "var(--bg-canvas)",
    border: `1px solid ${lens === "eng" ? "var(--db-navy-600)" : "var(--border)"}`,
    borderRadius: "var(--radius-lg)",
    padding: 16,
    color: lens === "eng" ? "var(--fg-on-dark)" : "var(--fg-1)",
  };
}
function memoryRow(lens: Lens): CSSProperties {
  return {
    border: `1px solid ${lens === "eng" ? "var(--db-navy-600)" : "var(--border)"}`,
    background: lens === "eng" ? "var(--db-navy-700)" : "var(--bg-subtle)",
    borderRadius: "var(--radius-md)",
    padding: "11px 13px",
  };
}
const sqlPre: CSSProperties = {
  margin: "0 0 12px",
  fontFamily: "var(--font-mono)",
  fontSize: 10.5,
  lineHeight: 1.6,
  color: "var(--db-oat-light)",
  overflowX: "auto",
};
