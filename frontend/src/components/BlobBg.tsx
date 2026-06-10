import type { CSSProperties } from "react";

/** Drifting gradient blobs — the V0 background. Mount once, behind everything. */
export function BlobBg() {
  const blob = (c: string, s: number, top: string, left: string, delay: string): CSSProperties => ({
    position: "absolute",
    width: s,
    height: s,
    top,
    left,
    background: `radial-gradient(circle at 30% 30%, ${c}, transparent 70%)`,
    filter: "blur(60px)",
    opacity: 0.45,
    animation: `drift 18s var(--ease-out) infinite`,
    animationDelay: delay,
  });
  return (
    <div aria-hidden style={{ position: "fixed", inset: 0, overflow: "hidden", zIndex: 0, pointerEvents: "none" }}>
      <div style={blob("var(--db-lava-400)", 520, "-8%", "-6%", "0s")} />
      <div style={blob("var(--db-navy-300)", 600, "40%", "62%", "-6s")} />
      <div style={blob("var(--db-yellow-400, #ffcc66)", 380, "68%", "8%", "-11s")} />
    </div>
  );
}
