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


def _stream_writer():
    """Return the LangGraph custom-stream writer, or None when not in a streaming context.

    get_stream_writer() RAISES outside a runnable context (not returns None), so guard with
    try/except — this keeps the invoke path and unit tests safe."""
    try:
        from langgraph.config import get_stream_writer
        return get_stream_writer()
    except Exception:
        return None


def _substep(node: str, label: str) -> None:
    if w := _stream_writer():
        w({"kind": "substep", "node": node, "label": label})


def _use_stubs() -> bool:
    return os.environ.get("USE_STUBS", "0") == "1"


# ── Knowledge ─────────────────────────────────────────────────────────────────────────

def knowledge_node(state: AgentState) -> dict:
    question = state["question"]
    _substep("gather_knowledge", "Searching corpus…")
    if _use_stubs():
        result = query_knowledge_fake(question)
        _substep("gather_knowledge", f"{len(result.passages)} passages")
        return {"knowledge_result": result}
    try:
        from agent_server.tools.knowledge_tool import query_knowledge_impl
        result = query_knowledge_impl(question)
        _substep("gather_knowledge", f"{len(result.passages)} passages")
        return {"knowledge_result": result}
    except Exception as exc:  # one tool down must not 500 the whole run
        logger.warning("knowledge gather failed (degrading to empty): %s", exc)
        _substep("gather_knowledge", "0 passages")
        return {"knowledge_result": KnowledgeResult(query=question, passages=[])}


# ── Analytics (Genie) ─────────────────────────────────────────────────────────────────

def analytics_node(state: AgentState) -> dict:
    question = state["question"]
    _substep("gather_analytics", "Genie: submitting query…")
    if _use_stubs():
        result = ask_genie_fake(question)
        # GenieResult.rows is Optional — a text-only / clarification answer has rows=None. Guard
        # the count: an unguarded len(None) here used to throw, get caught below, and discard a
        # perfectly good answer as "analytics gather failed".
        _substep("gather_analytics", f"Genie: {len(result.rows or [])} rows, SQL ready")
        return {"analytics_result": result}
    try:
        from agent_server.tools.genie_tool import ask_genie_impl
        result = ask_genie_impl(question)
        # GenieResult.rows is Optional — a text-only / clarification answer has rows=None. Guard
        # the count: an unguarded len(None) here used to throw, get caught below, and discard a
        # perfectly good answer as "analytics gather failed".
        _substep("gather_analytics", f"Genie: {len(result.rows or [])} rows, SQL ready")
        return {"analytics_result": result}
    except Exception as exc:  # degrade, don't fail the run
        logger.warning("analytics gather failed (degrading to empty): %s", exc)
        _substep("gather_analytics", "Genie: 0 rows, SQL ready")
        return {"analytics_result": GenieResult(question=question, error=str(exc))}


# ── Operational (Lakebase hybrid — STUB always; real impl in operational_tool) ───────

def operational_node(state: AgentState) -> dict:
    question = state["question"]
    _substep("gather_operational", "Running operational query…")
    if _use_stubs():
        result = query_operational_fake(question)
        _substep("gather_operational", f"{len(result.rows)} matches")
        return {"operational_result": result}
    try:
        from agent_server.tools.operational_tool import query_operational_impl
        result = query_operational_impl(question)
        _substep("gather_operational", f"{len(result.rows)} matches")
        return {"operational_result": result}
    except Exception as exc:  # degrade, don't fail the run
        logger.warning("operational gather failed (degrading to empty): %s", exc)
        _substep("gather_operational", "0 matches")
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
