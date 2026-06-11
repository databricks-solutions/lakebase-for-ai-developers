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


def _stream_writer():
    """Return the LangGraph custom-stream writer, or None when not in a streaming context.

    get_stream_writer() RAISES outside a runnable context (not returns None), so guard with
    try/except — this keeps the invoke path and unit tests safe."""
    try:
        from langgraph.config import get_stream_writer
        return get_stream_writer()
    except Exception:
        return None


_SYSTEM_PROMPT = """\
You are the supervisor (router) for a supply-chain planning copilot. Given a planner's
question, choose which gather agents should run. Return the RouterDecision schema:
`agents` (any of "knowledge", "analytics", "operational") and `reasoning` (one sentence).

<routing_principle>
Pick the MINIMAL set of agents that fully answers the question. Every extra agent adds
latency and noise, so do not add an agent unless the question genuinely needs what it
provides. Always include at least one.
</routing_principle>

<agents>
- knowledge: semantic search over PDFs (contracts, supplier notifications, competitor
  catalogs, promotion briefs, market events). Use for questions about specific documents,
  contract terms, supplier announcements, or market events.
- analytics: NL→SQL AGGREGATION over governed tables (suppliers, inventory, POs,
  supplier_status, product_dim) via Genie. Use ONLY when the question asks for an aggregate
  across many rows — a count, sum, total, ranking, average, or trend ("total open POs by
  supplier", "which suppliers are flagged at risk", "top 5 SKUs by...").
- operational: hybrid similarity + relational query against Lakebase. Finds SIMILAR PAST
  QUALITY ISSUES / incidents and ALREADY JOINS each match to that supplier/SKU's live
  inventory, open POs, and the caller's access scope — in one query. Use for "find similar
  past cases / quality issues", optionally with their current inventory/PO context.
</agents>

<boundary>
The operational agent already returns live inventory and open POs for the suppliers/SKUs
it matches. Do NOT add analytics just because a question mentions "inventory" or "open
POs" — only add analytics when the question asks for an AGGREGATE across many rows (a
total, count, ranking, or trend).
</boundary>

The planner's OWN prior decisions are ALWAYS recalled from long-term memory after the
gather phase, so you never route to memory. For questions that refer to earlier
conversations ("what did we decide…", "continue this morning's escalation", "yesterday"),
still pick the gather agents relevant to the underlying topic.

<examples>
<example>
Question: "Find similar past quality issues for the Acme gasket SKU-4400, joined to what we have on hand and on order."
agents: ["operational"]
reasoning: The operational agent finds similar past incidents and already joins live inventory and open POs in one query, so analytics is not needed.
</example>
<example>
Question: "What is the total on-hand inventory by product category right now?"
agents: ["analytics"]
reasoning: This is an aggregate rollup across many rows — a Genie NL→SQL job.
</example>
<example>
Question: "What do our Boeing and GE contracts say about late-delivery penalties?"
agents: ["knowledge"]
reasoning: Contract terms live in the document corpus.
</example>
<example>
Question: "A steel supplier announced a price increase — find the related market-event note and any similar past incidents, and recommend whether to pre-buy."
agents: ["knowledge", "operational"]
reasoning: The market-event note is a document (knowledge) and the similar past incidents come from the operational store.
</example>
</examples>
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
    note = f"supervisor → {decision.agents}: {decision.reasoning}"
    notes = state.get("trace_notes", []) or []
    notes = [*notes, note]
    if w := _stream_writer():
        w({"kind": "route", "agents": decision.agents, "reasoning": decision.reasoning})
        w({"kind": "trace", "note": note})
    return {"route_decision": decision, "trace_notes": notes}
