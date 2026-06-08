import { useEffect, useState, type CSSProperties } from "react";
import { getExplorer, peek } from "../api";
import type { ExplorerCard, ExplorerData } from "../types";

const ACCENTS: Record<string, string> = {
  navy: "var(--db-navy-600)",
  lava: "var(--db-lava-600)",
  blue: "var(--db-blue-600)",
  green: "var(--db-green-600)",
  yellow: "var(--db-yellow-600)",
  maroon: "var(--db-maroon-600)",
};

/** Right-hand drawer: one card per backend component, each with deep links + a live peek. */
export function ExplorerDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [data, setData] = useState<ExplorerData | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (open && !data) getExplorer().then(setData).catch((e) => setErr(String(e)));
  }, [open, data]);

  return (
    <>
      {open && (
        <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(11,32,38,0.28)", zIndex: 40 }} />
      )}
      <aside
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          height: "100%",
          width: 440,
          maxWidth: "92vw",
          background: "var(--bg-canvas)",
          borderLeft: "1px solid var(--border)",
          boxShadow: "var(--shadow-lg)",
          transform: open ? "translateX(0)" : "translateX(100%)",
          transition: "transform var(--dur-slow) var(--ease-out)",
          zIndex: 41,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <header style={{ padding: "var(--space-5)", borderBottom: "1px solid var(--border)" }}>
          <div className="eyebrow">Backend</div>
          <h3 style={{ marginTop: 4 }}>What's under the hood</h3>
          <p style={{ color: "var(--fg-2)", fontSize: "var(--fs-body-sm)", marginTop: 6 }}>
            Every component this agent runs on — open it in Databricks or peek inline.
          </p>
        </header>
        <div style={{ padding: "var(--space-4)", overflowY: "auto", display: "grid", gap: "var(--space-4)" }}>
          {err && <p style={{ color: "var(--danger)" }}>{err}</p>}
          {data?.cards.map((c) => <ResourceCard key={c.key} card={c} />)}
          {!data && !err && <p style={{ color: "var(--fg-2)" }}>Loading…</p>}
        </div>
      </aside>
    </>
  );
}

function ResourceCard({ card }: { card: ExplorerCard }) {
  const accent = ACCENTS[card.accent] ?? "var(--db-navy-600)";
  const [peekData, setPeekData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const runPeek = () => {
    if (!card.peek) return;
    setLoading(true);
    peek(card.peek).then(setPeekData).catch((e) => setPeekData({ error: String(e) })).finally(() => setLoading(false));
  };

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", overflow: "hidden", background: "var(--bg-canvas)" }}>
      <div style={{ height: 4, background: accent }} />
      <div style={{ padding: "var(--space-4)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
          <strong style={{ fontSize: "var(--fs-body-lg)" }}>{card.title}</strong>
          {card.link && (
            <a href={card.link} target="_blank" rel="noreferrer" style={{ fontSize: "var(--fs-body-sm)", whiteSpace: "nowrap" }}>
              {card.link_label ?? "Open"} ↗
            </a>
          )}
        </div>
        <p style={{ color: "var(--fg-2)", fontSize: "var(--fs-body-sm)", margin: "4px 0 10px" }}>{card.subtitle}</p>
        <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 12px", margin: 0, fontSize: "var(--fs-caption)" }}>
          {Object.entries(card.facts).map(([k, v]) => (
            <FactRow key={k} k={k} v={v} />
          ))}
        </dl>
        {card.peek && (
          <div style={{ marginTop: 10 }}>
            <button onClick={runPeek} disabled={loading} style={peekBtn}>
              {loading ? "Loading…" : peekData ? "Refresh peek" : "Peek inside"}
            </button>
            {peekData && <PeekResult data={peekData} />}
          </div>
        )}
      </div>
    </div>
  );
}

function FactRow({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt style={{ color: "var(--fg-3)" }}>{k}</dt>
      <dd style={{ margin: 0, fontFamily: "var(--font-mono)", color: "var(--fg-1)", wordBreak: "break-all" }}>{v}</dd>
    </>
  );
}

function PeekResult({ data }: { data: any }) {
  if (data.error) return <p style={{ color: "var(--danger)", fontSize: "var(--fs-caption)", marginTop: 8 }}>{data.error}</p>;
  const rows: any[] = data.sample ?? data.tables ?? [];
  const meta = data.active_rows != null ? `${data.active_rows} active rows` : data.tables ? `${rows.length} tables` : "";
  return (
    <div style={{ marginTop: 8, background: "var(--db-navy-900)", color: "var(--db-oat-light)", borderRadius: "var(--radius-md)", padding: "var(--space-3)", overflow: "auto", maxHeight: 220 }}>
      {meta && <div style={{ color: "var(--db-navy-300)", fontSize: 11, marginBottom: 6 }}>{meta}</div>}
      <pre style={{ margin: 0, fontSize: 11, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
        {rows.slice(0, 8).map((r, i) => JSON.stringify(r)).join("\n") || "(empty)"}
      </pre>
    </div>
  );
}

const peekBtn: CSSProperties = {
  fontFamily: "var(--font-sans)",
  fontSize: "var(--fs-body-sm)",
  padding: "6px 12px",
  borderRadius: "var(--radius-pill)",
  border: "1px solid var(--border-strong)",
  background: "var(--bg-subtle)",
  color: "var(--fg-1)",
  cursor: "pointer",
};
