"""Shared LangGraph state for the supervisor agent.

Per CLAUDE.md: gather agents write DISTINCT state keys (no reducer); the planner fan-out
writes the shared `plans` key (reducer required) — but planner-level fan-out isn't in P0,
so we skip the reducer for now. Bulk payloads (rows, passages) stay on state for v1; if
state grows too large, move them to side tables and keep refs only.
"""

from __future__ import annotations

from typing_extensions import TypedDict

from agent_server.contracts import (
    GenieResult,
    HITLDecision,
    KnowledgeResult,
    OperationalResult,
    PlannerRecommendation,
    RouterDecision,
)


class AgentState(TypedDict, total=False):
    # ── Input ────────────────────────────────────────────────────────────────────────────
    question: str  # the user's message
    user_id: str  # OBO; threaded through to operational + audit

    # ── Routing (supervisor sets this) ───────────────────────────────────────────────────
    route_decision: RouterDecision

    # ── Gather phase (each agent writes its own key; no reducer per CLAUDE.md) ───────────
    knowledge_result: KnowledgeResult
    analytics_result: GenieResult
    operational_result: OperationalResult

    # ── Planner ──────────────────────────────────────────────────────────────────────────
    recommendation: PlannerRecommendation

    # ── HITL ─────────────────────────────────────────────────────────────────────────────
    hitl_decision: HITLDecision

    # ── Audit / trace ────────────────────────────────────────────────────────────────────
    trace_notes: list[str]  # human-readable breadcrumbs (planner reasoning, route reason)
