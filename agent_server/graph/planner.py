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
- `commit_node`: P0 long-term memory is write-back-only — persist the verdict to the Lakebase store
  (no hydrate-and-use yet).
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent_server.config import settings
from agent_server.contracts import HITLDecision, HITLVerdict, PlannerRecommendation
from agent_server.graph.state import AgentState

logger = logging.getLogger(__name__)

# Above this estimated cost the gate trips and routes to HITL approval.
APPROVAL_COST_THRESHOLD_USD = 50_000.0


def approvals_namespace(user_id: str) -> tuple[str, str]:
    """The long-term store namespace for a user's prior decisions. Single source of truth so
    `commit_node` (write) and the memory recall node (read) always agree. LangGraph store
    namespace labels can't contain '.', so map it to '-'."""
    return ("approvals", (user_id or "unknown").replace(".", "-"))

_SYSTEM_PROMPT = """\
You are the planner for a supply-chain planning copilot. Given a planner's question and the
evidence gathered by retrieval/analytics/operational agents — plus the planner's own prior
decisions recalled from long-term memory — produce a concise, actionable recommendation.

If the question refers to an earlier conversation ("what did we decide…", "continue this
morning's escalation", "last time"), ground your answer in the recalled prior decisions and
say what was decided before; only escalate again if genuinely new action is needed.

Return fields:
- summary: one-line recommendation a planner can act on.
- actions: 1-5 concrete, ordered steps (e.g. "Expedite a 200-unit reorder of SKU-1001 from an
  alternate supplier").
- is_action_bearing: true if the recommendation COMMITS SPEND or is RISKY/IRREVERSIBLE — it tells
  the planner to reorder, expedite, re-source, pre-buy, quarantine, or hold. false for purely
  INFORMATIONAL answers that only report/look up/aggregate (counts, totals, status, "which
  suppliers are at risk", "what do the contracts say"). When in doubt, prefer true.
- est_cost_usd: your best dollar estimate of executing the recommended actions (reorder / expedite
  / re-source cost). Return 0 for a purely informational answer (no spend committed). Only return
  null if it is action-bearing but you genuinely cannot estimate.
- reasoning: 1-3 sentences justifying the recommendation from the evidence.

Be specific and ground every claim in the provided evidence. Do not invent SKUs, suppliers, or numbers."""


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
    if (mr := state.get("memory_result")) and mr.memories:
        lines = [
            f"  - [{m.verdict or 'decision'}] {m.question or ''} → {m.summary or ''}"
            + (f" (note: {m.note})" if m.note else "")
            for m in mr.memories[:5]
        ]
        parts.append(
            "Your prior decisions (long-term memory — recall and build on these when the "
            "question refers to earlier conversations):\n" + "\n".join(lines)
        )
    return "\n\n".join(parts) if parts else "No gather results were returned."


def _llm_draft(question: str, evidence: str) -> _PlannerDraft | None:
    """Try the LLM planner. Returns None on any failure so the caller can fall back."""
    try:
        from databricks_langchain import ChatDatabricks
    except ImportError:
        return None
    try:
        # NB: no temperature — Opus-class reasoning models reject the param (BAD_REQUEST).
        llm = ChatDatabricks(endpoint=settings.llm_planner_endpoint)
        structured = llm.with_structured_output(_PlannerDraft)
        return structured.invoke(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Question:\n{question}\n\nEvidence:\n{evidence}"},
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
    draft = _llm_draft(question, evidence) or _fallback_draft(state, question)

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
    notes = state.get("trace_notes", []) or []
    notes = [
        *notes,
        f"planner → needs_approval={rec.needs_approval} "
        f"(action_bearing={rec.is_action_bearing}, est {cost})",
    ]
    return {"recommendation": rec, "trace_notes": notes}


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
    notes = state.get("trace_notes", []) or []
    notes = [*notes, f"hitl_review → {decision.verdict.value} by {decision.user_id}"]
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
    """Finalize. P0 long-term memory is write-back-only: persist the verdict to the Lakebase
    store (hydrate-and-use is parked). No-op if no store is configured (offline tests)."""
    decision = state.get("hitl_decision")
    rec = state.get("recommendation")
    user_id = state.get("user_id", "unknown")

    configurable = config.get("configurable", {}) if config else {}
    store = configurable.get("store")
    thread_id = configurable.get("thread_id", "unknown")

    persisted = False
    if store is not None:
        try:
            namespace = approvals_namespace(user_id)
            value = {
                "question": state.get("question"),
                "recommendation": rec.model_dump() if rec else None,
                "verdict": decision.verdict.value if decision else None,
                "note": decision.note if decision else None,
            }
            await store.aput(namespace, thread_id, value)
            persisted = True
        except Exception as exc:  # never fail the run on a memory write
            logger.warning("Commit write-back to store failed: %s", exc)

    notes = state.get("trace_notes", []) or []
    notes = [*notes, f"commit → finalized (verdict={decision.verdict.value if decision else 'n/a'}, "
                     f"store_write={'ok' if persisted else 'skipped'})"]
    return {"trace_notes": notes}
