"""Long-term memory hydration node.

Runs *after* the gather fan-in and *before* the planner so it can scope supplier-note recall
to the suppliers the operational agent actually surfaced. Reads the Lakebase store from
`config["configurable"]["store"]` (wired in `agent.py`), runs three scoped semantic searches
concurrently, and writes the `memory_context` state key for the planner to consume.

Memory is best-effort: any failure (or a missing store, e.g. offline tests) yields an empty
`MemoryContext` and a trace breadcrumb — it never fails the run.
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.runnables import RunnableConfig

from agent_server.config import settings
from agent_server.contracts import MemoryContext
from agent_server.graph.state import AgentState
from agent_server.memory import (
    recall_approvals,
    recall_preferences,
    recall_supplier_notes,
)

logger = logging.getLogger(__name__)


async def hydrate_memory_node(state: AgentState, config: RunnableConfig) -> dict:
    """Hydrate long-term memory (preferences + prior approvals + supplier notes) into state."""
    notes = state.get("trace_notes", []) or []
    configurable = config.get("configurable", {}) if config else {}
    store = configurable.get("store")

    if store is None:
        return {
            "memory_context": MemoryContext(),
            "trace_notes": [*notes, "hydrate_memory → skipped (no store)"],
        }

    user_id = state.get("user_id", "unknown")
    query = state.get("question", "") or ""
    k = settings.memory_recall_limit
    threshold = settings.memory_similarity_threshold

    op = state.get("operational_result")
    supplier_ids = [r.supplier_id for r in (op.rows if op else []) or [] if r.supplier_id]

    try:
        prefs, approvals, supplier_notes = await asyncio.gather(
            recall_preferences(store, user_id, query, k, threshold),
            recall_approvals(store, user_id, query, k, threshold),
            recall_supplier_notes(store, supplier_ids, query, k, threshold),
        )
    except Exception as exc:  # pragma: no cover - defensive; recall helpers already guard
        logger.warning("hydrate_memory failed: %s", exc)
        return {
            "memory_context": MemoryContext(),
            "trace_notes": [*notes, f"hydrate_memory → error: {exc}"],
        }

    ctx = MemoryContext(
        preferences=prefs, prior_approvals=approvals, supplier_notes=supplier_notes
    )
    summary = (
        f"hydrate_memory → prefs={len(prefs)} approvals={len(approvals)} "
        f"supplier_notes={len(supplier_notes)}"
    )
    return {"memory_context": ctx, "trace_notes": [*notes, summary]}


__all__ = ["hydrate_memory_node"]
