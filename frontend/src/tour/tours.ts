// Guided-tour registry — single source of truth for the walkthrough.
// Each step targets an element by its `data-tour="<value>"` attribute (added across the UI
// components). A null target renders a centered modal step. Ported (simplified for one page)
// from the strategic_revenue_demo tour engine.

export type TooltipSide = "top" | "bottom" | "left" | "right";

export interface TourStep {
  target: string | null;
  title: string;
  body: string;
  side?: TooltipSide;
}

export interface Tour {
  id: string;
  label: string;
  steps: TourStep[];
}

export const TOURS: Record<string, Tour> = {
  overview: {
    id: "overview",
    label: "Overview",
    steps: [
      {
        target: null,
        title: "Welcome to your Planner Copilot",
        body: "A 30-second tour of the agent and what's running underneath it. Use Next / Back, or Exit anytime.",
      },
      {
        target: "composer",
        side: "top",
        title: "Ask anything",
        body: "Type a supply-chain question here — supplier risk, quality issues, inventory, open POs. Press Enter to send. The agent routes it across pgvector, Genie and Vector Search.",
      },
      {
        target: "suggestions",
        side: "top",
        title: "Or start from an example",
        body: "Not sure where to begin? Click a suggested question — the Henkel one walks the headline hybrid pgvector + operational-join scenario.",
      },
      {
        target: "nav-review",
        side: "bottom",
        title: "Review — the decision surface",
        body: "When the planner proposes a fix, it pauses here for a human. Approve, hold, or edit each action individually, record a rationale, and commit. Partial decisions are first-class — approve some, hold one — and nothing writes until you commit.",
      },
      {
        target: "nav-lakebase",
        side: "bottom",
        title: "Lakebase — what the decision changed",
        body: "The durable state behind the agent: approved actions, planning-parameter overrides, constraints, and the recalled decisions in agent memory. Flip the Engineer/Planner lens to read the same rows as raw Postgres or in plain language.",
      },
      {
        target: "inspect",
        side: "bottom",
        title: "Peer into the backend",
        body: "Open the explorer to see a card for every Databricks component the agent runs on — Lakebase, pgvector, the MLflow experiment, Vector Search, Genie and Unity Catalog — each with a deep link and a live peek.",
      },
      {
        target: "history",
        side: "right",
        title: "Your conversations",
        body: "Every chat is one Lakebase thread, so context persists across turns. Reopen a past conversation here, or start a new one.",
      },
      {
        target: "identity",
        side: "right",
        title: "Who you are",
        body: "The agent runs on-behalf-of you — your identity scopes which operational rows you're allowed to see. This shows the signed-in user.",
      },
      {
        target: null,
        title: "You're set",
        body: "Click “Take a tour” in the header anytime to see this again. Now ask your first question.",
      },
    ],
  },
};
