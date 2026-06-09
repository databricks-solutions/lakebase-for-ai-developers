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

from langchain_core.runnables import RunnableConfig

from agent_server.contracts import (
    GenieResult,
    KnowledgeResult,
    MemoryItem,
    MemoryResult,
    OperationalResult,
)
from agent_server.graph.state import AgentState
from agent_server.tools.stubs import (
    ask_genie_fake,
    query_knowledge_fake,
    query_operational_fake,
    recall_memory_fake,
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


# ── Memory (long-term recall over the Lakebase store — hydrate-and-use) ───────────────

async def memory_node(state: AgentState, config: RunnableConfig) -> dict:
    """Recall the user's own prior decisions from the long-term store and expose them to the
    planner. Always runs (hydrate-and-use): the planner uses them when the question refers to
    an earlier conversation, and ignores them otherwise. Never fails the run — degrades to
    empty on any store error."""
    question = state.get("question", "")
    user_id = state.get("user_id", "unknown")
    if _use_stubs():
        return {"memory_result": recall_memory_fake(question)}

    from agent_server.graph.planner import approvals_namespace

    configurable = config.get("configurable", {}) if config else {}
    store = configurable.get("store")
    memories: list[MemoryItem] = []
    if store is not None:
        try:
            # The store has embeddings configured, so a query does semantic recall over the
            # user's prior decisions; namespace matches what commit_node writes.
            items = await store.asearch(approvals_namespace(user_id), query=question, limit=5)
            for it in items:
                value = getattr(it, "value", None) or {}
                rec = value.get("recommendation") or {}
                memories.append(
                    MemoryItem(
                        thread_id=getattr(it, "key", None),
                        question=value.get("question"),
                        summary=rec.get("summary") if isinstance(rec, dict) else None,
                        verdict=value.get("verdict"),
                        note=value.get("note"),
                        score=getattr(it, "score", None),
                    )
                )
        except Exception as exc:  # never fail the run on a recall miss
            logger.warning("memory recall failed: %s", exc)
    return {"memory_result": MemoryResult(query=question, memories=memories)}


# ── Router for the gather fan-out (used by build_graph) ───────────────────────────────

def route_to_gatherers(state: AgentState) -> list[str]:
    """Map the supervisor's RouterDecision to the gather node names. Returning a list of
    node names from a conditional edge fans the graph out to all of them.

    `gather_memory` is ALWAYS included (hydrate-and-use long-term memory), even when the
    router doesn't name it, so every plan can build on the user's prior decisions."""
    decision = state["route_decision"]
    mapping = {
        "knowledge": "gather_knowledge",
        "analytics": "gather_analytics",
        "operational": "gather_operational",
        "memory": "gather_memory",
    }
    targets = [mapping[a] for a in decision.agents if a in mapping]
    targets = targets or ["gather_knowledge"]
    if "gather_memory" not in targets:
        targets.append("gather_memory")
    return targets
