"""Gather nodes — one per data surface.

Each node writes a DISTINCT state key (no reducer per CLAUDE.md). They run together in
a single superstep (the supervisor's conditional edge fans out to a list of these node
names), then converge on the planner.

Each node has a real impl and a stub. Switch with `USE_STUBS=1` in env — useful while
the workspace resources (VS index, Genie space, Lakebase) aren't all wired yet.
"""

from __future__ import annotations

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


def _use_stubs() -> bool:
    return os.environ.get("USE_STUBS", "0") == "1"


# ── Knowledge ─────────────────────────────────────────────────────────────────────────

def knowledge_node(state: AgentState) -> dict:
    question = state["question"]
    if _use_stubs():
        result: KnowledgeResult = query_knowledge_fake(question)
    else:
        from agent_server.tools.knowledge_tool import query_knowledge_impl
        result = query_knowledge_impl(question)
    return {"knowledge_result": result}


# ── Analytics (Genie) ─────────────────────────────────────────────────────────────────

def analytics_node(state: AgentState) -> dict:
    question = state["question"]
    if _use_stubs():
        result: GenieResult = ask_genie_fake(question)
    else:
        from agent_server.tools.genie_tool import ask_genie_impl
        result = ask_genie_impl(question)
    return {"analytics_result": result}


# ── Operational (Lakebase hybrid — STUB always; WS2 replaces) ────────────────────────

def operational_node(state: AgentState) -> dict:
    question = state["question"]
    user_id = state.get("user_id", "unknown")
    if _use_stubs():
        result: OperationalResult = query_operational_fake(question, user_id)
    else:
        from agent_server.tools.operational_tool import query_operational_impl
        result = query_operational_impl(question, user_id)
    return {"operational_result": result}


# ── Router for the gather fan-out (used by build_graph) ───────────────────────────────

def route_to_gatherers(state: AgentState) -> list[str]:
    """Map the supervisor's RouterDecision to the gather node names. Returning a list of
    node names from a conditional edge fans the graph out to all of them."""
    decision = state["route_decision"]
    mapping = {
        "knowledge": "gather_knowledge",
        "analytics": "gather_analytics",
        "operational": "gather_operational",
    }
    targets = [mapping[a] for a in decision.agents if a in mapping]
    # Safety: a route_decision with no recognized agents would otherwise stall the graph.
    return targets or ["gather_knowledge"]
