import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { ChatMessage } from "../types";

const SUGGESTIONS = [
  "Show me similar quality issues for Henkel, joined to on-hand inventory and open POs",
  "Which suppliers are at risk right now?",
  "Total open PO quantity by supplier for Q4",
];

export function ChatPanel({
  messages,
  busy,
  onSend,
}: {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const submit = () => {
    const t = draft.trim();
    if (!t || busy) return;
    onSend(t);
    setDraft("");
  };

  const empty = messages.length === 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", position: "relative", zIndex: 1 }}>
      <div style={{ flex: 1, overflowY: "auto", padding: "var(--space-6) var(--space-5)" }}>
        <div style={{ maxWidth: 760, margin: "0 auto", display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
          {empty ? (
            <Welcome onPick={(s) => onSend(s)} />
          ) : (
            messages.map((m) => <Bubble key={m.id} m={m} />)
          )}
          <div ref={endRef} />
        </div>
      </div>

      <div style={{ borderTop: "1px solid var(--border)", background: "rgba(249,247,244,0.85)", backdropFilter: "blur(6px)", padding: "var(--space-4) var(--space-5)" }}>
        <div style={{ maxWidth: 760, margin: "0 auto" }}>
          <div data-tour="composer" style={{ display: "flex", gap: 10, alignItems: "flex-end", border: "1px solid var(--border-strong)", borderRadius: "var(--radius-xl)", background: "var(--bg-canvas)", padding: 8, boxShadow: "var(--shadow-sm)" }}>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
              placeholder="Ask the supply-chain planner…"
              rows={1}
              style={{
                flex: 1, resize: "none", border: "none", outline: "none", background: "transparent",
                font: "inherit", color: "var(--fg-1)", padding: "8px 10px", maxHeight: 160,
              }}
            />
            <button onClick={submit} disabled={busy || !draft.trim()} style={sendBtn(busy || !draft.trim())}>
              {busy ? "…" : "Send"}
            </button>
          </div>
          <p style={{ textAlign: "center", color: "var(--fg-3)", fontSize: 11, marginTop: 8 }}>
            Runs the LangGraph supervisor on Databricks · state persists in Lakebase by conversation
          </p>
        </div>
      </div>
    </div>
  );
}

function Welcome({ onPick }: { onPick: (s: string) => void }) {
  return (
    <div style={{ textAlign: "center", padding: "var(--space-8) 0", animation: "fade-up var(--dur-slow) var(--ease-out)" }}>
      <div className="eyebrow">Supply-Chain Planner Copilot</div>
      <h1 style={{ margin: "10px 0 8px" }}>How can I help you plan today?</h1>
      <p style={{ color: "var(--fg-2)", maxWidth: 520, margin: "0 auto var(--space-6)" }}>
        Ask about supplier risk, quality issues, inventory and open POs. I route across pgvector,
        Genie and Vector Search, then propose an action for your approval.
      </p>
      <div data-tour="suggestions" style={{ display: "grid", gap: 10, maxWidth: 620, margin: "0 auto" }}>
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => onPick(s)} style={suggestionBtn}>{s}</button>
        ))}
      </div>
    </div>
  );
}

function Bubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === "user";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: isUser ? "flex-end" : "flex-start", animation: "fade-up var(--dur-base) var(--ease-out)" }}>
      <div className="eyebrow" style={{ marginBottom: 4 }}>{isUser ? "You" : "Planner Copilot"}</div>
      <div
        style={{
          maxWidth: "100%",
          background: isUser ? "var(--db-navy-800)" : "var(--bg-canvas)",
          color: isUser ? "var(--fg-on-dark)" : "var(--fg-1)",
          border: isUser ? "none" : "1px solid var(--border)",
          borderRadius: "var(--radius-lg)",
          padding: "var(--space-4) var(--space-5)",
          boxShadow: "var(--shadow-sm)",
          whiteSpace: "pre-wrap",
          lineHeight: "var(--lh-relaxed)",
        }}
      >
        {m.error ? <span style={{ color: "var(--danger)" }}>{m.text}</span>
          : m.pending ? <Thinking />
          : m.text}
      </div>
      {m.extras && !m.pending && <Extras extras={m.extras} />}
    </div>
  );
}

function Thinking() {
  return (
    <span style={{ color: "var(--fg-2)" }}>
      Thinking<span style={{ animation: "cursor-blink 1s steps(1) infinite" }}>▋</span>
    </span>
  );
}

function Extras({ extras }: { extras: NonNullable<ChatMessage["extras"]> }) {
  const chips: string[] = [];
  if (extras.route) chips.push(`route: ${extras.route}`);
  if (extras.status) chips.push(extras.status);
  const appr = extras.approval_request;
  return (
    <div style={{ marginTop: 8, maxWidth: "100%", display: "grid", gap: 8 }}>
      {chips.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {chips.map((c) => <span key={c} style={chip}>{c}</span>)}
        </div>
      )}
      {appr && (
        <div style={{ border: "1px solid var(--db-yellow-600)", background: "#fff8e8", borderRadius: "var(--radius-lg)", padding: "var(--space-4)" }}>
          <div className="eyebrow" style={{ color: "var(--db-yellow-700)" }}>Approval requested</div>
          <p style={{ margin: "6px 0", fontWeight: 500 }}>{appr.summary ?? "This action needs sign-off."}</p>
          {appr.est_cost_usd != null && <p style={{ color: "var(--fg-2)", fontSize: "var(--fs-body-sm)" }}>Est. cost: ${Number(appr.est_cost_usd).toLocaleString()}</p>}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button style={{ ...sendBtn(false), background: "var(--db-green-600)" }}>Approve</button>
            <button style={{ ...peekBtn }}>Reject</button>
          </div>
          <p style={{ color: "var(--fg-3)", fontSize: 11, marginTop: 8 }}>
            HITL resume wiring is in progress (follow-up #2) — buttons are a placeholder.
          </p>
        </div>
      )}
      {extras.operational_sql && (
        <details style={{ fontSize: "var(--fs-body-sm)" }}>
          <summary style={{ cursor: "pointer", color: "var(--fg-2)" }}>Generated SQL</summary>
          <pre style={{ background: "var(--db-navy-900)", color: "var(--db-oat-light)", padding: 12, borderRadius: 8, overflow: "auto", marginTop: 6 }}>{extras.operational_sql}</pre>
        </details>
      )}
      {extras.trace_notes && extras.trace_notes.length > 0 && (
        <details style={{ fontSize: "var(--fs-body-sm)" }}>
          <summary style={{ cursor: "pointer", color: "var(--fg-2)" }}>How I got here ({extras.trace_notes.length} steps)</summary>
          <ol style={{ margin: "6px 0 0 18px", color: "var(--fg-2)" }}>
            {extras.trace_notes.map((t, i) => <li key={i}>{t}</li>)}
          </ol>
        </details>
      )}
    </div>
  );
}

const chip: CSSProperties = {
  fontFamily: "var(--font-mono)", fontSize: 11, padding: "2px 8px",
  borderRadius: "var(--radius-pill)", background: "var(--bg-subtle)", color: "var(--fg-2)",
};
const suggestionBtn: CSSProperties = {
  font: "inherit", textAlign: "left", padding: "12px 16px", borderRadius: "var(--radius-lg)",
  border: "1px solid var(--border)", background: "var(--bg-canvas)", color: "var(--fg-1)",
  cursor: "pointer", boxShadow: "var(--shadow-sm)",
};
const peekBtn: CSSProperties = {
  font: "inherit", padding: "8px 16px", borderRadius: "var(--radius-pill)",
  border: "1px solid var(--border-strong)", background: "var(--bg-subtle)", color: "var(--fg-1)", cursor: "pointer",
};
function sendBtn(disabled: boolean): CSSProperties {
  return {
    font: "inherit", fontWeight: 500, padding: "8px 18px", borderRadius: "var(--radius-pill)",
    border: "none", background: disabled ? "var(--db-navy-300)" : "var(--db-lava-600)",
    color: "var(--db-white)", cursor: disabled ? "default" : "pointer",
  };
}
