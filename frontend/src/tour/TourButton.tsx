import { useTour } from "./useTour";

/** "Take a tour" control for the header. */
export function TourButton({ tourId = "overview", label = "Take a tour" }: { tourId?: string; label?: string }) {
  const { startTour } = useTour();
  return (
    <button
      type="button"
      onClick={() => startTour(tourId)}
      title="Guided walkthrough"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "8px 14px",
        borderRadius: "var(--radius-pill)",
        border: "1px solid var(--border-strong)",
        background: "var(--bg-canvas)",
        color: "var(--fg-1)",
        font: "inherit",
        fontSize: "var(--fs-body-sm)",
        fontWeight: 500,
        cursor: "pointer",
        boxShadow: "var(--shadow-sm)",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--db-lava-600)"; e.currentTarget.style.color = "var(--db-lava-700)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border-strong)"; e.currentTarget.style.color = "var(--fg-1)"; }}
    >
      <span aria-hidden style={{ color: "var(--db-lava-600)" }}>✦</span> {label}
    </button>
  );
}
