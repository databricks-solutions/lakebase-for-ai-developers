"""Gather nodes — one per data surface.

Each node writes a DISTINCT state key (no reducer per CLAUDE.md). They run together in
a single superstep (the supervisor's conditional edge fans out to a list of these node
names), then converge on the planner.

Each node has a real impl and a stub. Switch with `USE_STUBS=1` in env — useful while
the workspace resources (VS index, Genie space, Lakebase) aren't all wired yet.
"""

from __future__ import annotations

import logging
import os

from agent_server.contracts import (
    GenieResult,
    KnowledgeResult,
    OperationalResult,
)
from agent_server.graph.state import AgentState
from agent_server.tools.stubs import (
    ask_genie_fake,
    query_knowledge_fake,
    query_operational_fake,
)

logger = logging.getLogger(__name__)


def _use_stubs() -> bool:
    return os.environ.get("USE_STUBS", "0") == "1"


# ── Knowledge ─────────────────────────────────────────────────────────────────────────

def knowledge_node(state: AgentState) -> dict:
    question = state["question"]
    if _use_stubs():
        return {"knowledge_result": query_knowledge_fake(question)}
    try:
        from agent_server.tools.knowledge_tool import query_knowledge_impl
        return {"knowledge_result": query_knowledge_impl(question)}
    except Exception as exc:  # one tool down must not 500 the whole run
        logger.warning("knowledge gather failed (degrading to empty): %s", exc)
        return {"knowledge_result": KnowledgeResult(query=question, passages=[])}


# ── Analytics (Genie) ─────────────────────────────────────────────────────────────────

def analytics_node(state: AgentState) -> dict:
    question = state["question"]
    if _use_stubs():
        return {"analytics_result": ask_genie_fake(question)}
    try:
        from agent_server.tools.genie_tool import ask_genie_impl
        return {"analytics_result": ask_genie_impl(question)}
    except Exception as exc:  # degrade, don't fail the run
        logger.warning("analytics gather failed (degrading to empty): %s", exc)
        return {"analytics_result": GenieResult(question=question, error=str(exc))}


# ── Operational (Lakebase hybrid — STUB always; WS2 replaces) ────────────────────────

def operational_node(state: AgentState) -> dict:
    question = state["question"]
    user_id = state.get("user_id", "unknown")
    if _use_stubs():
        return {"operational_result": query_operational_fake(question, user_id)}
    try:
        from agent_server.tools.operational_tool import query_operational_impl
        return {"operational_result": query_operational_impl(question, user_id)}
    except Exception as exc:  # degrade, don't fail the run
        logger.warning("operational gather failed (degrading to empty): %s", exc)
        return {"operational_result": OperationalResult(question=question, sql="", rows=[])}


# ── Router for the gather fan-out (used by build_graph) ───────────────────────────────

def route_to_gatherers(state: AgentState) -> list[str]:
    """Map the supervisor's RouterDecision to the gather node names. Returning a list of
    node names from a conditional edge fans the graph out to all of them.

    Long-term memory is NOT a gather agent — it is hydrated after the gather fan-in by
    `hydrate_memory_node`, so it is intentionally absent from this mapping."""
    decision = state["route_decision"]
    mapping = {
        "knowledge": "gather_knowledge",
        "analytics": "gather_analytics",
        "operational": "gather_operational",
    }
    targets = [mapping[a] for a in decision.agents if a in mapping]
    return targets or ["gather_knowledge"]
