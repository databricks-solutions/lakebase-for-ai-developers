"""Planner + gate.

P0 stub: produces a deterministic PlannerRecommendation from whatever gather results are
on state. WS4 owns the real implementation (LLM call against the strong planner endpoint,
threshold logic, cost estimation). The gate flips `needs_approval` based on a simple
cost heuristic so the HITL interrupt path is exercised end-to-end.
"""

from __future__ import annotations

from agent_server.contracts import PlannerRecommendation
from agent_server.graph.state import AgentState


# Above this threshold the gate trips and routes to HITL approval. WS4 will refine.
APPROVAL_COST_THRESHOLD_USD = 50_000.0


def planner_node(state: AgentState) -> dict:
    """Compose a recommendation. Real impl lands in WS4."""
    citations: list[str] = []
    actions: list[str] = []
    summary_parts: list[str] = []

    if (kr := state.get("knowledge_result")) and kr.passages:
        citations += [p.source for p in kr.passages[:3]]
        summary_parts.append(f"{len(kr.passages)} relevant passages")
    if (ar := state.get("analytics_result")) and ar.rows:
        if ar.sql:
            citations.append(f"analytics-sql:{hash(ar.sql) % 10_000:04d}")
        summary_parts.append(f"{len(ar.rows)} analytics rows")
    if (or_ := state.get("operational_result")) and or_.rows:
        summary_parts.append(f"{len(or_.rows)} operational matches")
        actions.append("Review the matched operational cases for similarity to live SKUs.")

    summary = (
        "STUB recommendation — " + ", ".join(summary_parts)
        if summary_parts
        else "STUB recommendation — no gather results returned."
    )
    if not actions:
        actions = ["Confirm scope with the planner before proceeding."]

    est_cost = 75_000.0  # stub — WS4 derives from the actions
    rec = PlannerRecommendation(
        summary=summary,
        actions=actions,
        needs_approval=est_cost >= APPROVAL_COST_THRESHOLD_USD,
        est_cost_usd=est_cost,
        reasoning="P0 stub planner: deterministic compose from gather results.",
        citations=citations,
    )

    notes = state.get("trace_notes", []) or []
    notes = [*notes, f"planner → needs_approval={rec.needs_approval} (est ${rec.est_cost_usd:,.0f})"]
    return {"recommendation": rec, "trace_notes": notes}


def gate_router(state: AgentState) -> str:
    """Conditional edge after planner: HITL if approval required, else straight to commit."""
    rec = state.get("recommendation")
    if rec and rec.needs_approval:
        return "hitl_review"
    return "commit"


def hitl_review_node(state: AgentState) -> dict:
    """P0 stub HITL node — autoapprove. Real `interrupt()` lands when WS1 wires the
    checkpointer (the durable pause is meaningless without a checkpointer)."""
    from agent_server.contracts import HITLDecision, HITLVerdict

    notes = state.get("trace_notes", []) or []
    notes = [*notes, "hitl_review → auto-approved (STUB; real interrupt() lands with checkpointer)"]
    return {
        "hitl_decision": HITLDecision(
            verdict=HITLVerdict.APPROVED,
            note="auto-approved by P0 stub",
            user_id=state.get("user_id", "unknown"),
        ),
        "trace_notes": notes,
    }


def commit_node(state: AgentState) -> dict:
    """Final node — would write the decision to durable storage. P0 stub: trace only."""
    notes = state.get("trace_notes", []) or []
    notes = [*notes, "commit → recommendation finalized (STUB; long-term store wired in P1)"]
    return {"trace_notes": notes}
