"""Shared LangGraph state for the supervisor agent.

Per CLAUDE.md: gather agents write DISTINCT state keys (no reducer); the planner fan-out
writes the shared `plans` key (reducer required) — but planner-level fan-out isn't in P0,
so we skip the reducer for now. Bulk payloads (rows, passages) stay on state for v1; if
state grows too large, move them to side tables and keep refs only.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from agent_server.contracts import (
    GenieResult,
    HITLDecision,
    KnowledgeResult,
    MemoryContext,
    OperationalResult,
    PlannerRecommendation,
    RouterDecision,
)


class AgentState(TypedDict, total=False):
    # ── Input ────────────────────────────────────────────────────────────────────────────
    question: str  # the user's message
    user_id: str  # caller identity — namespaces long-term memory + audit write-back

    # ── Short-term memory (WS1): conversational history accumulated per thread via the ───
    # checkpointer. The entrypoints append a HumanMessage per turn; the planner appends an
    # AIMessage(summary) and reads a trimmed `_history_block` so follow-ups resolve referents
    # ("that SKU"). add_messages is the append reducer.
    messages: Annotated[list[AnyMessage], add_messages]

    # ── Routing (supervisor sets this) ───────────────────────────────────────────────────
    route_decision: RouterDecision

    # ── Gather phase (each agent writes its own key; no reducer per CLAUDE.md) ───────────
    knowledge_result: KnowledgeResult
    analytics_result: GenieResult
    operational_result: OperationalResult

    # ── Long-term memory (hydrated after gather fan-in, before the planner; no reducer) ──
    memory_context: MemoryContext

    # ── Planner ──────────────────────────────────────────────────────────────────────────
    recommendation: PlannerRecommendation

    # ── HITL ─────────────────────────────────────────────────────────────────────────────
    hitl_decision: HITLDecision

    # ── Commit (Meridian write-back; no reducer) ─────────────────────────────────────────
    # commit_node stashes what got written to the Lakebase write-back tables (per-table row
    # counts + the staged rows) for the done payload + the UI's Lakebase tab.
    commit_ledger: dict

    # ── Audit / trace ────────────────────────────────────────────────────────────────────
    trace_notes: list[str]  # human-readable breadcrumbs (planner reasoning, route reason)
