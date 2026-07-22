import type { CSSProperties } from "react";
import { Database, Info, MessageSquare, ShieldCheck } from "lucide-react";
import type { Page } from "../App";

const NAV: { id: Page; label: string; icon: React.ReactNode }[] = [
  { id: "chat", label: "Chat", icon: <MessageSquare size={15} /> },
  { id: "review", label: "Review", icon: <ShieldCheck size={15} /> },
  { id: "lakebase", label: "Lakebase", icon: <Database size={15} /> },
  { id: "about", label: "About", icon: <Info size={15} /> },
];

/**
 * Lightweight top-level switcher (no router). Ported from the Meridian mockup's NavSide
 * structure, restyled to tokens. A dot on Review signals a plan is awaiting a decision.
 */
export function TopNav({
  page,
  setPage,
  reviewBadge,
}: {
  page: Page;
  setPage: (p: Page) => void;
  reviewBadge?: boolean;
}) {
  return (
    <nav data-tour="topnav" style={navWrap}>
      {NAV.map((n) => {
        const on = page === n.id;
        const showDot = n.id === "review" && reviewBadge && !on;
        return (
          <button key={n.id} onClick={() => setPage(n.id)} style={navItem(on)} data-tour={`nav-${n.id}`}>
            {n.icon}
            <span style={{ fontWeight: 600, fontSize: "var(--fs-body-sm)" }}>{n.label}</span>
            {showDot && <span style={badgeDot} />}
          </button>
        );
      })}
    </nav>
  );
}

const navWrap: CSSProperties = {
  display: "inline-flex",
  gap: 3,
  padding: 3,
  borderRadius: "var(--radius-pill)",
  background: "var(--bg-subtle)",
  border: "1px solid var(--border)",
};

function navItem(on: boolean): CSSProperties {
  return {
    position: "relative",
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    border: "none",
    borderRadius: "var(--radius-pill)",
    padding: "7px 14px",
    font: "inherit",
    cursor: "pointer",
    background: on ? "var(--bg-canvas)" : "transparent",
    color: on ? "var(--fg-1)" : "var(--fg-2)",
    boxShadow: on ? "var(--shadow-sm)" : "none",
  };
}

const badgeDot: CSSProperties = {
  width: 7,
  height: 7,
  borderRadius: 9,
  background: "var(--db-yellow-600)",
  animation: "cursor-blink 1.6s ease-in-out infinite",
};
