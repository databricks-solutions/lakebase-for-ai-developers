"""Supervisor / router node.

Classifies the user's question into a set of gather agents to invoke. Uses
ChatDatabricks (router-tier endpoint per `.env`) with a structured-output schema bound to
`RouterDecision`. Falls back to a deterministic keyword router if the LLM call fails or
the endpoint isn't configured, so the graph stays runnable in offline / stub mode.

This is the only node that calls an LLM in the gather phase — gather agents themselves
use their respective Databricks-managed services (VS / Genie / Lakebase).
"""

from __future__ import annotations

from agent_server.config import settings
from agent_server.contracts import RouterDecision
from agent_server.graph.state import AgentState


_SYSTEM_PROMPT = """\
You are the supervisor for a supply-chain planning copilot. Given a planner's question,
decide which subset of the gather agents to invoke. Return STRICT JSON matching the
RouterDecision schema with fields: agents (list of any of "knowledge", "analytics",
"operational") and reasoning (one sentence).

The planner's OWN prior decisions are ALWAYS recalled from long-term memory automatically
(after the gather phase) — you do not route to it. For questions that refer to earlier
conversations ("what did we decide…", "continue this morning's escalation", "yesterday"),
still pick the gather agents relevant to the underlying topic.

Agent purposes:
- knowledge: semantic search over PDFs (contracts, supplier notifications, competitor
  catalogs, promotion briefs, market events). Use for questions about specific documents,
  contract terms, supplier announcements, market events.
- analytics: NL→SQL aggregation over operational tables (suppliers, inventory, POs,
  supplier_status, product_dim) via Genie. Use for counts, sums, rollups, trend questions.
- operational: hybrid similarity + relational query against Lakebase memory + live
  operational rows. Use for "find similar past quality issues / cases" joined to current
  inventory or POs.

Most non-trivial questions invoke 2+ agents. Always include at least one.
"""


def _llm_route(question: str) -> RouterDecision | None:
    """Try the LLM route. Returns None on any failure so the caller can fall back."""
    try:
        from databricks_langchain import ChatDatabricks
    except ImportError:
        return None
    try:
        # NB: no temperature — Opus-class reasoning models reject the param (BAD_REQUEST).
        llm = ChatDatabricks(endpoint=settings.llm_router_endpoint)
        structured = llm.with_structured_output(RouterDecision)
        return structured.invoke(
            [{"role": "system", "content": _SYSTEM_PROMPT},
             {"role": "user", "content": question}]
        )
    except Exception:
        return None


def _keyword_route(question: str) -> RouterDecision:
    """Deterministic fallback. Keeps the graph runnable without a workspace."""
    q = question.lower()
    agents: list = []
    # Heuristic mapping — good enough for stubs and tests.
    if any(t in q for t in ("contract", "supplier notif", "market event", "promotion", "competitor", "price increase", "raw material")):
        agents.append("knowledge")
    if any(t in q for t in ("total", "sum", "how many", "rank", "top", "trend", "by quarter", "by region", "average", "open po", "on hand", "inventory")):
        agents.append("analytics")
    if any(t in q for t in ("similar", "past quality", "quality issue", "incident", "comparable case", "prior")):
        agents.append("operational")
    # Long-term memory is hydrated automatically after gather (not a routable agent), so
    # continuation cues ("what did we decide…") only steer the topical gather agents above.
    if not agents:
        # Default — without LLM and without keyword hits, hit both retrieval surfaces.
        agents = ["knowledge", "analytics"]
    return RouterDecision(
        agents=agents,
        reasoning="keyword-fallback routing (LLM unavailable or no endpoint configured)",
    )


def supervisor_node(state: AgentState) -> dict:
    """Set `route_decision` based on the question. The downstream conditional edge
    fans out to the chosen gather nodes."""
    question = state.get("question", "")
    decision = _llm_route(question) or _keyword_route(question)
    notes = state.get("trace_notes", []) or []
    notes = [*notes, f"supervisor → {decision.agents}: {decision.reasoning}"]
    return {"route_decision": decision, "trace_notes": notes}
