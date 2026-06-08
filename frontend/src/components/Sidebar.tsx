import type { CSSProperties } from "react";
import type { Me, Session } from "../types";

/** Left rail: brand, new-chat, the user's conversation history, and who they are. */
export function Sidebar({
  me,
  sessions,
  currentThread,
  onNew,
  onOpen,
}: {
  me: Me | null;
  sessions: Session[];
  currentThread: string;
  onNew: () => void;
  onOpen: (threadId: string) => void;
}) {
  return (
    <aside
      style={{
        width: 264,
        flexShrink: 0,
        background: "var(--bg-inverse)",
        color: "var(--fg-on-dark)",
        display: "flex",
        flexDirection: "column",
        position: "relative",
        zIndex: 1,
      }}
    >
      <div style={{ padding: "var(--space-5) var(--space-4)" }}>
        <div className="eyebrow" style={{ color: "var(--db-lava-400)" }}>Databricks · Lakebase</div>
        <div style={{ fontSize: "var(--fs-h4)", fontWeight: 500, marginTop: 4, lineHeight: 1.2 }}>
          Supply-Chain<br />Planner Copilot
        </div>
      </div>

      <div style={{ padding: "0 var(--space-4) var(--space-4)" }}>
        <button onClick={onNew} style={newBtn}>+ New conversation</button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "0 var(--space-3)" }}>
        <div className="eyebrow" style={{ color: "var(--fg-on-dark-2)", padding: "0 var(--space-2) var(--space-2)" }}>History</div>
        {sessions.length === 0 && (
          <p style={{ color: "var(--fg-on-dark-2)", fontSize: "var(--fs-body-sm)", padding: "0 var(--space-2)" }}>
            No conversations yet.
          </p>
        )}
        {sessions.map((s) => {
          const active = s.thread_id === currentThread;
          return (
            <button
              key={s.thread_id}
              onClick={() => onOpen(s.thread_id)}
              title={s.preview || s.title}
              style={{
                display: "block", width: "100%", textAlign: "left", font: "inherit",
                color: "var(--fg-on-dark)", background: active ? "var(--db-navy-700)" : "transparent",
                border: "none", borderRadius: "var(--radius-md)", padding: "10px 10px",
                cursor: "pointer", marginBottom: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}
            >
              {s.title || "Untitled"}
            </button>
          );
        })}
      </div>

      <div style={{ borderTop: "1px solid var(--db-navy-700)", padding: "var(--space-4)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 32, height: 32, borderRadius: "var(--radius-pill)", background: "var(--db-lava-600)", color: "#fff", display: "grid", placeItems: "center", fontWeight: 600 }}>
            {(me?.email ?? "?").slice(0, 1).toUpperCase()}
          </div>
          <div style={{ overflow: "hidden" }}>
            <div style={{ fontSize: "var(--fs-body-sm)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {me?.email ?? "…"}
            </div>
            <div style={{ fontSize: 11, color: "var(--fg-on-dark-2)" }}>
              {me?.is_local ? "local dev" : "on-behalf-of-user"}
            </div>
          </div>
        </div>
        {me && !me.in_scope && (
          <p style={{ fontSize: 11, color: "var(--db-yellow-400, #ffcc66)", marginTop: 8 }}>
            Heads up: data is scoped to {me.demo_planner_user}. Sign in as them to see the hero scenario.
          </p>
        )}
      </div>
    </aside>
  );
}

const newBtn: CSSProperties = {
  width: "100%", font: "inherit", fontWeight: 500, padding: "10px 14px",
  borderRadius: "var(--radius-pill)", border: "1px solid var(--db-navy-600)",
  background: "transparent", color: "var(--fg-on-dark)", cursor: "pointer",
};
