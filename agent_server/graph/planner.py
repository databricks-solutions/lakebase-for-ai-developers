"""Planner + gate + HITL + commit.

- `planner_node`: an LLM (strong planner endpoint) composes a `PlannerRecommendation` from the
  gather results — summary, ordered actions, an estimated cost, reasoning. Citations are collected
  deterministically from the gather results (never hallucinated), and `needs_approval` is decided
  deterministically by the gate threshold (not the LLM) so the escalation is reliable. Falls back
  to a deterministic compose if the LLM/endpoint is unavailable, so the graph stays runnable offline.
- `gate_router`: routes to HITL when approval is required, else straight to commit.
- `hitl_review_node`: a real `interrupt()` — the run pauses durably on the checkpoint, surfacing the
  recommendation as an approval card; it resumes when the app injects an HITLDecision via
  `Command(resume=...)`.
- `commit_node`: persist curated long-term memory to the Lakebase store via the policy in
  `agent_server.memory` (audit every decision; learn preferences + supplier notes only from approved
  action-bearing outcomes). Recall happens upstream in `hydrate_memory_node`, injected here via
  `_memory_block`. Best-effort: a memory failure never fails the run.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent_server.config import settings
from agent_server.contracts import HITLDecision, HITLVerdict, PlannerRecommendation
from agent_server.graph.state import AgentState
from agent_server.memory import build_memory_writes, write_memories

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


# Above this estimated cost the gate trips and routes to HITL approval.
APPROVAL_COST_THRESHOLD_USD = 50_000.0

_SYSTEM_PROMPT = """\
You are the planner for a supply-chain planning copilot. Using the gathered evidence — plus
the planner's own prior decisions recalled from long-term memory — produce a concise,
actionable recommendation.

<grounding>
Ground every claim in the provided evidence. Do not invent SKUs, suppliers, quantities, or
statuses. If the evidence does not contain what is needed to answer, say so explicitly (e.g.
"the available data does not show supplier risk flags") and recommend what to gather next.
An ungrounded recommendation is worse than naming the gap, because a planner may act on it.
</grounding>

<continuity>
If the question refers to an earlier conversation ("what did we decide…", "continue this
morning's escalation", "last time"), ground your answer in the recalled prior decisions and
say what was decided before; only escalate again if genuinely new action is needed.
</continuity>

Return these fields:
- summary: one-line recommendation a planner can act on.
- actions: 1-5 concrete, ordered steps (e.g. "Expedite a 200-unit reorder of SKU-1001 from an
  alternate supplier").
- is_action_bearing: whether the recommendation COMMITS NEW SPEND or is RISKY/IRREVERSIBLE
  (reorder, expedite, re-source, pre-buy, quarantine, or hold for the first time). Set true
  only when proposing a NEW commitment beyond what was already decided — BUT a recommendation
  about WHETHER to commit a new spend or risk (e.g. whether to pre-buy ahead of a price
  increase, re-source, or expedite) gates that spend decision, so treat it as action-bearing.
  When genuinely in doubt about a new action, prefer true — under-escalating a spend decision
  is worse than an extra approval. See the examples below.
- est_cost_usd: your best dollar estimate of executing the recommended actions (reorder /
  expedite / re-source cost). Return 0 for a purely informational answer (no spend committed).
  Return null only if it is action-bearing but you genuinely cannot estimate.
- reasoning: 1-3 sentences justifying the recommendation from the evidence.

<is_action_bearing_examples>
- "Which suppliers are currently flagged at risk?" -> false (informational lookup; reports status, commits nothing).
- "What do our contracts say about late-delivery penalties?" -> false (informational).
- "Remind me what we decided this morning about SKU-1001." -> false (recalls an existing decision).
- "Recommend a mitigation for the recurring adhesive cracking." -> true (proposes new action: reorder / expedite / quarantine).
- "Find related notes and similar incidents, and recommend whether to pre-buy ahead of the price increase." -> true (gates a new spend decision, even if you advise caution).
</is_action_bearing_examples>"""


class _PlannerDraft(BaseModel):
    """LLM-authored portion of the recommendation. `needs_approval` + `citations` are set by code."""

    summary: str
    actions: list[str] = Field(default_factory=list)
    is_action_bearing: bool = True  # commits spend / risky-irreversible vs purely informational
    est_cost_usd: float | None = None
    reasoning: str | None = None


def _collect_citations(state: AgentState) -> list[str]:
    """Citations come from the gather results, not the LLM (traceability/scoring)."""
    citations: list[str] = []
    if (kr := state.get("knowledge_result")) and kr.passages:
        citations += [p.source for p in kr.passages[:3]]
    if (ar := state.get("analytics_result")) and ar.sql:
        citations.append(f"analytics-sql:{hash(ar.sql) % 10_000:04d}")
    if (or_ := state.get("operational_result")) and or_.sql:
        citations.append(f"operational-sql:{hash(or_.sql) % 10_000:04d}")
    return citations


def _evidence_block(state: AgentState) -> str:
    """Render the gather results into a compact prompt context."""
    parts: list[str] = []
    if (or_ := state.get("operational_result")) and or_.rows:
        lines = [
            f"  - {r.supplier_id}/{r.sku} (sim={r.similarity}): {r.summary} "
            f"[on_hand={r.on_hand_qty}, open_po={r.open_po_qty}]"
            for r in or_.rows[:5]
        ]
        parts.append("Operational matches (similar quality issues + live inventory/POs):\n" + "\n".join(lines))
    if (ar := state.get("analytics_result")) and ar.rows:
        parts.append(f"Analytics ({ar.answer or 'rows'}):\n  " + "\n  ".join(str(r) for r in ar.rows[:8]))
    if (kr := state.get("knowledge_result")) and kr.passages:
        lines = [f"  - [{p.source}] {(p.content or '')[:160]}" for p in kr.passages[:4]]
        parts.append("Knowledge passages:\n" + "\n".join(lines))
    return "\n\n".join(parts) if parts else "No gather results were returned."


def _memory_block(state: AgentState) -> str:
    """Render hydrated long-term memory into a compact prompt context (mirrors `_evidence_block`).

    The hydrate node recalls the planner's own prior decisions; the system prompt tells the LLM to
    build on them when the question refers to an earlier conversation, and ignore them otherwise."""
    ctx = state.get("memory_context")
    if not ctx or ctx.is_empty:
        return ""
    parts: list[str] = []
    if ctx.preferences:
        parts.append(
            "Planner preferences (from prior approved decisions):\n"
            + "\n".join(f"  - {m.text}" for m in ctx.preferences)
        )
    if ctx.prior_approvals:
        parts.append(
            "Relevant past decisions:\n" + "\n".join(f"  - {m.text}" for m in ctx.prior_approvals)
        )
    if ctx.supplier_notes:
        parts.append(
            "Known supplier notes:\n" + "\n".join(f"  - {m.text}" for m in ctx.supplier_notes)
        )
    return "\n\n".join(parts)


def _history_block(state: AgentState) -> str:
    """Render the recent conversation turns into a compact prompt context (WS1 short-term memory).

    The current turn's question is passed to the planner separately, so exclude it here and show
    only the prior turns — trimmed to the last `short_term_keep_recent` (older turns are dropped,
    not summarized, for now). This lets follow-ups resolve referents ("that SKU", "the same
    supplier") from earlier in the same conversation."""
    msgs = state.get("messages") or []
    prior = msgs[:-1]  # drop the just-appended HumanMessage for the current question
    if not prior:
        return ""
    recent = prior[-settings.short_term_keep_recent:]
    lines = []
    for m in recent:
        role = "User" if getattr(m, "type", "") == "human" else "Assistant"
        content = m.content if isinstance(m.content, str) else str(m.content)
        lines.append(f"  {role}: {content[:300]}")
    return "Earlier in this conversation:\n" + "\n".join(lines)


def _llm_draft(question: str, evidence: str, memory: str = "", history: str = "") -> _PlannerDraft | None:
    """Try the LLM planner. Returns None on any failure so the caller can fall back."""
    try:
        from databricks_langchain import ChatDatabricks
    except ImportError:
        return None
    try:
        # NB: no temperature — Opus-class reasoning models reject the param (BAD_REQUEST).
        llm = ChatDatabricks(endpoint=settings.llm_planner_endpoint)
        structured = llm.with_structured_output(_PlannerDraft)
        # Longform context first (memory/history/evidence), question + task last — per
        # Anthropic long-context guidance — each block wrapped in XML tags so the model
        # parses instructions vs. data unambiguously.
        sections: list[str] = []
        if memory:
            # Memory is advisory: use it to personalize, but ground claims in current evidence.
            sections.append(f"<recalled_memory>\n{memory}\n</recalled_memory>")
        if history:
            # Short-term conversation context — resolve follow-up referents from earlier turns.
            sections.append(f"<conversation_history>\n{history}\n</conversation_history>")
        sections.append(f"<evidence>\n{evidence}\n</evidence>")
        sections.append(
            f"<question>\n{question}\n</question>\n\n"
            "Produce the recommendation. Use recalled memory only to personalize; ground "
            "every factual claim in the evidence above, and name any gap rather than guessing."
        )
        user_content = "\n\n".join(sections)
        return structured.invoke(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ]
        )
    except Exception as exc:
        logger.warning("Planner LLM unavailable, falling back to deterministic compose: %s", exc)
        return None


def _fallback_draft(state: AgentState, question: str) -> _PlannerDraft:
    counts = []
    if (kr := state.get("knowledge_result")) and kr.passages:
        counts.append(f"{len(kr.passages)} passages")
    if (ar := state.get("analytics_result")) and ar.rows:
        counts.append(f"{len(ar.rows)} analytics rows")
    if (or_ := state.get("operational_result")) and or_.rows:
        counts.append(f"{len(or_.rows)} operational matches")
    return _PlannerDraft(
        summary="Recommendation (deterministic compose) — " + (", ".join(counts) or "no gather results"),
        actions=["Review the matched operational cases and confirm scope before proceeding."],
        est_cost_usd=None,
        reasoning="LLM planner unavailable; composed from gather results.",
    )


def planner_node(state: AgentState) -> dict:
    """Compose a recommendation via the planner LLM (with deterministic gate + citations)."""
    question = state.get("question", "")
    evidence = _evidence_block(state)
    memory = _memory_block(state)
    history = _history_block(state)
    if w := _stream_writer():
        w({"kind": "substep", "node": "planner", "label": "Composing recommendation…"})
    draft = _llm_draft(question, evidence, memory, history) or _fallback_draft(state, question)

    # Deterministic gate: a recommendation needs human approval when it COMMITS SPEND / is
    # risky-irreversible (is_action_bearing), OR when a known cost clears the threshold. Purely
    # informational answers (is_action_bearing=False, cost 0/None) are NOT gated — this is the
    # fix for over-escalation, where an unknown cost alone used to force approval on every run.
    over_threshold = (
        draft.est_cost_usd is not None and draft.est_cost_usd >= APPROVAL_COST_THRESHOLD_USD
    )
    needs_approval = draft.is_action_bearing or over_threshold

    rec = PlannerRecommendation(
        summary=draft.summary,
        actions=draft.actions or ["Confirm scope with the planner before proceeding."],
        needs_approval=needs_approval,
        is_action_bearing=draft.is_action_bearing,
        est_cost_usd=draft.est_cost_usd,
        reasoning=draft.reasoning,
        citations=_collect_citations(state),
    )

    cost = f"${rec.est_cost_usd:,.0f}" if rec.est_cost_usd is not None else "unknown"
    note = (
        f"planner → needs_approval={rec.needs_approval} "
        f"(action_bearing={rec.is_action_bearing}, est {cost}, memory={'used' if memory else 'none'}, "
        f"history={'used' if history else 'none'})"
    )
    notes = state.get("trace_notes", []) or []
    notes = [*notes, note]
    if w := _stream_writer():
        w({"kind": "trace", "note": note})
    # Record the assistant turn (the recommendation one-liner) into short-term history so the
    # NEXT turn on this thread can resolve follow-up referents. add_messages appends it.
    return {"recommendation": rec, "trace_notes": notes,
            "messages": [AIMessage(content=rec.summary)]}


def gate_router(state: AgentState) -> str:
    """Conditional edge after planner: HITL if approval required, else straight to commit."""
    rec = state.get("recommendation")
    if rec and rec.needs_approval:
        return "hitl_review"
    return "commit"


def hitl_review_node(state: AgentState) -> dict:
    """Real HITL: pause durably with `interrupt()`, surfacing the recommendation as an approval
    card. Resumes when the app injects an HITLDecision via `Command(resume=...)`."""
    rec = state.get("recommendation")
    resume_value = interrupt(
        {
            "type": "approval_request",
            "recommendation": rec.model_dump() if rec else None,
            "prompt": "Approve or reject this recommendation.",
        }
    )

    # `resume_value` is whatever the app passed to Command(resume=...): an HITLDecision dict.
    decision = _coerce_decision(resume_value, state.get("user_id", "unknown"))
    note = f"hitl_review → {decision.verdict.value} by {decision.user_id}"
    notes = state.get("trace_notes", []) or []
    notes = [*notes, note]
    if w := _stream_writer():
        w({"kind": "trace", "note": note})
    return {"hitl_decision": decision, "trace_notes": notes}


def _coerce_decision(resume_value, user_id: str) -> HITLDecision:
    if isinstance(resume_value, HITLDecision):
        return resume_value
    if isinstance(resume_value, dict):
        return HITLDecision(
            verdict=HITLVerdict(resume_value.get("verdict", "approved")),
            note=resume_value.get("note"),
            user_id=resume_value.get("user_id") or user_id,
        )
    # Fallback for a bare verdict string or unexpected shape.
    try:
        return HITLDecision(verdict=HITLVerdict(str(resume_value)), user_id=user_id)
    except ValueError:
        return HITLDecision(verdict=HITLVerdict.APPROVED, note="defaulted", user_id=user_id)


async def commit_node(state: AgentState, config: RunnableConfig) -> dict:
    """Finalize. Persist curated long-term memory per `agent_server.memory.build_memory_writes`
    (audit every decision; learn preferences + supplier notes only from approved action-bearing
    outcomes). No-op if no store is configured (offline tests). Never fails the run on a write."""
    decision = state.get("hitl_decision")

    configurable = config.get("configurable", {}) if config else {}
    store = configurable.get("store")
    thread_id = configurable.get("thread_id", "unknown")

    writes = build_memory_writes(state, decision, thread_id)
    counts = await write_memories(store, writes)

    verdict = decision.verdict.value if decision else "n/a"
    written = ", ".join(f"{k}={v}" for k, v in counts.items()) if counts else "skipped"
    note = f"commit → finalized (verdict={verdict}, memory_writes={written})"
    notes = state.get("trace_notes", []) or []
    notes = [*notes, note]
    if w := _stream_writer():
        w({"kind": "trace", "note": note})
    return {"trace_notes": notes}
