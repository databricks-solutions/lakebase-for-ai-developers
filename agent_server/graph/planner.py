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

import asyncio
import logging
import re

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent_server.config import settings
from agent_server.contracts import (
    ActionDecision,
    ActionFact,
    ActionKind,
    HITLDecision,
    HITLVerdict,
    PlannedAction,
    PlannerRecommendation,
)
from agent_server.graph.history import render_history
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
</is_action_bearing_examples>

<structured_plan>
When the recommendation is action-bearing, ALWAYS ALSO return `planned_actions`: at most 4
concrete, reviewable actions, each grounded in the evidence block. Never return only free-text
`actions` — every action-bearing recommendation MUST populate `planned_actions` so a planner can
review and commit each one. Pick the playbook that fits the question.

QUALITY playbook — for quality questions (recurring defects, cracking, adhesion/thermal
failures, a supplier's repeated quality issues). Contain the defect, then optionally re-source:
- QUALITY_HOLD — hold the on-hand units pending validation. Set `sku`/`supplier_id` and `qty`
  to the units to hold (the on-hand count from the matched row).
- QUARANTINE_PO — quarantine/inspect an incoming PO before it lands. Cite the `po_id` from the
  operational rows; set `qty` to the PO units and `supplier_id`/`sku`.
- TIGHTEN_INSPECTION — raise the incoming-inspection level for the SKU. Set `sku` and `qty` to
  the proposed inspection percentage.
- SUPPLIER_QUALITY_HOLD — rule: hold this supplier's SKU until quality is validated. Set
  `supplier_id` and `sku`; put scope + the "until validated" condition in `facts`.
- (optional) SPLIT_SOURCE — bridge order from the ALTERNATE supplier (e.g. DuPont) to re-source
  while the primary is held. Set `supplier_id` to the alternate, plus `sku`/`qty`.

SHORTAGE playbook — for shortage questions (coverage gap, supplier delay, force-majeure):
- EXPEDITE_PO — pull in an existing open PO. Cite the `po_id`; set `qty`, `supplier_id`, `sku`.
- SPLIT_SOURCE — buffer order from the ALTERNATE supplier (not the flagged primary). Set
  `supplier_id` to the alternate, plus `sku` and `qty`.
- RAISE_SAFETY_STOCK — bump the SKU's safety stock. Set `sku` and `qty` to the proposed level.
- ALLOCATION_CONSTRAINT — prioritize a program/customer for the constrained SKU. Set `sku` and
  `program`.

Worked QUALITY example — "Show me similar quality issues for Henkel (SUP-001/SKU-1001 epoxy
cracking), joined to on-hand inventory and open POs" with 40 on-hand and a 500-unit incoming PO:
1. QUALITY_HOLD — hold the 40 on-hand SKU-1001 units pending adhesion/thermal validation.
2. QUARANTINE_PO — quarantine the incoming 500-unit SUP-001 PO and inspect before receipt.
3. TIGHTEN_INSPECTION — raise SKU-1001 incoming-inspection level until the defect is contained.
4. SUPPLIER_QUALITY_HOLD — no new SUP-001/SKU-1001 acceptance until quality is validated.

Each action: a short `title`, a one-sentence `detail`, a `cost_delta` when you can estimate it,
and `facts` — 1-3 short evidence snippets ("on-hand: 40 units", "incoming PO: 500 units").
Do not invent po_ids, suppliers, or quantities — ground every field in the evidence.
If a relevant past decision is recalled (memory block non-empty), state in `reasoning` how this
plan builds on or differs from it.
</structured_plan>"""


class _PlannedActionDraft(BaseModel):
    """LLM-authored shape of one structured action. CODE deterministically maps this →
    PlannedAction (key, target_table, slider bounds, evidence_refs) so the durable contract is
    not at the model's mercy."""

    kind: ActionKind
    title: str
    detail: str
    qty: float | None = None
    cost_delta: float | None = None
    sku: str | None = None
    supplier_id: str | None = None
    po_id: str | None = None
    program: str | None = None
    facts: list[str] = Field(default_factory=list)


class _PlannerDraft(BaseModel):
    """LLM-authored portion of the recommendation. `needs_approval` + `citations` are set by code."""

    summary: str
    actions: list[str] = Field(default_factory=list)
    is_action_bearing: bool = True  # commits spend / risky-irreversible vs purely informational
    est_cost_usd: float | None = None
    reasoning: str | None = None
    planned_actions: list[_PlannedActionDraft] = Field(default_factory=list)


# Maps each action kind to its durable write-back table (decided by CODE, not the LLM).
_KIND_TO_TABLE: dict[ActionKind, str] = {
    ActionKind.EXPEDITE_PO: "approved_actions",
    ActionKind.SPLIT_SOURCE: "approved_actions",
    ActionKind.RAISE_SAFETY_STOCK: "planning_parameters",
    ActionKind.ALLOCATION_CONSTRAINT: "constraints",
    # Quality-containment kinds (Meridian quality pivot) routed onto the same three tables.
    ActionKind.QUALITY_HOLD: "approved_actions",
    ActionKind.QUARANTINE_PO: "approved_actions",
    ActionKind.TIGHTEN_INSPECTION: "planning_parameters",
    ActionKind.SUPPLIER_QUALITY_HOLD: "constraints",
}

# Safety-stock slider ceiling/step for the RAISE_SAFETY_STOCK editor (floor is the SKU's current
# on-hand from the operational rows, see `_safety_stock_floor`).
_SAFETY_STOCK_QTY_MAX = 2000.0
_SAFETY_STOCK_QTY_STEP = 10.0
_SAFETY_STOCK_FLOOR = 0.0


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


def _slug(value: str) -> str:
    """Lowercase, hyphen-joined slug fragment for a stable per-action key."""
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _action_key(draft: _PlannedActionDraft, index: int) -> str:
    """Stable per-action id: kind + the most specific identifier available (po/sku), else index."""
    tail = draft.po_id or draft.sku or draft.supplier_id or draft.program or str(index)
    return f"{_slug(draft.kind.value)}-{_slug(str(tail))}" if tail else _slug(draft.kind.value)


def _safety_stock_floor(state: AgentState, sku: str | None) -> float:
    """Slider floor for a RAISE_SAFETY_STOCK action: the SKU's current on-hand from the operational
    rows (you should never raise safety stock below what's already on the shelf), else a sane 0."""
    if (or_ := state.get("operational_result")) and or_.rows:
        for r in or_.rows:
            if (sku is None or r.sku == sku) and r.on_hand_qty is not None:
                return float(r.on_hand_qty)
    return _SAFETY_STOCK_FLOOR


def _on_hand_qty(state: AgentState, sku: str | None) -> float | None:
    """The SKU's current on-hand units from the operational rows, used as the default qty for a
    QUALITY_HOLD when the LLM didn't propose one ("hold the N units we already have")."""
    if (or_ := state.get("operational_result")) and or_.rows:
        for r in or_.rows:
            if (sku is None or r.sku == sku) and r.on_hand_qty is not None:
                return float(r.on_hand_qty)
    return None


def _fact_from_string(raw: str) -> ActionFact:
    """Turn a free-text fact string into an ActionFact. Split on the first ':' into k/v when the
    LLM gives a labelled fact ("on-hand: 40 units"); otherwise label it generically as 'note'."""
    text = (raw or "").strip()
    if ":" in text:
        k, v = text.split(":", 1)
        return ActionFact(k=k.strip() or "note", v=v.strip())
    return ActionFact(k="note", v=text)


def _to_planned_actions(draft: _PlannerDraft, state: AgentState) -> list[PlannedAction]:
    """PURE: map the LLM's `_PlannedActionDraft`s → durable `PlannedAction`s (key, target_table,
    editability, slider bounds, evidence_refs, facts). No LLM/DB — unit-testable in isolation."""
    evidence_refs = _collect_citations(state)
    out: list[PlannedAction] = []
    used_keys: set[str] = set()
    for i, d in enumerate(draft.planned_actions):
        target_table = _KIND_TO_TABLE[d.kind]
        # Keys must be UNIQUE per action: two actions of the same kind+identifier (e.g. two
        # allocation constraints on one SKU) would otherwise share a key and clobber each other on
        # commit (write-back PKs are keyed by this), collapsing N actions into one row. Disambiguate
        # a collision with the loop index so each action commits to its own row.
        key = _action_key(d, i)
        if key in used_keys:
            key = f"{key}-{i}"
        used_keys.add(key)
        action = PlannedAction(
            key=key,
            kind=d.kind,
            title=d.title,
            detail=d.detail,
            target_table=target_table,  # type: ignore[arg-type]
            editable=True,
            qty=d.qty,
            cost_delta=d.cost_delta,
            facts=[_fact_from_string(f) for f in d.facts],
            evidence_refs=evidence_refs,
            default_status="approve",
            sku=d.sku,
            supplier_id=d.supplier_id,
            po_id=d.po_id,
            program=d.program,
        )
        if d.kind == ActionKind.RAISE_SAFETY_STOCK:
            action.qty_label = "Safety stock (units)"
            action.qty_min = _safety_stock_floor(state, d.sku)
            action.qty_max = _SAFETY_STOCK_QTY_MAX
            action.qty_step = _SAFETY_STOCK_QTY_STEP
        elif d.kind == ActionKind.QUALITY_HOLD:
            # Hold the units we already have on the shelf; default to the SKU's on-hand qty.
            action.qty = d.qty if d.qty is not None else _on_hand_qty(state, d.sku)
            action.qty_label = "Units to hold"
            action.qty_min = 0.0
            action.qty_step = 10.0
        elif d.kind == ActionKind.QUARANTINE_PO:
            action.qty_label = "PO units"
            action.qty_min = 0.0
            action.qty_step = 50.0
        elif d.kind == ActionKind.TIGHTEN_INSPECTION:
            action.qty_label = "Inspection %"
            action.qty_min = 0.0
            action.qty_max = 100.0
            action.qty_step = 5.0
        elif d.kind == ActionKind.SUPPLIER_QUALITY_HOLD:
            # A rule, not a quantity edit: rely on facts (scope=SKU, until="validated").
            action.editable = False
        elif d.qty is not None:
            action.qty_label = "Units"
        out.append(action)
    return out


# Keyword → ActionKind for the deterministic free-text fallback. Order matters: the first
# matching (case-insensitive substring) keyword wins, so the more specific quality kinds are
# checked before the generic "hold". A line with no match defaults to QUALITY_HOLD.
_TEXT_KEYWORD_KINDS: list[tuple[tuple[str, ...], ActionKind]] = [
    (("quarantine",), ActionKind.QUARANTINE_PO),
    (("inspect",), ActionKind.TIGHTEN_INSPECTION),
    (("split", "alternate", "dupont", "bridge"), ActionKind.SPLIT_SOURCE),
    (("safety stock",), ActionKind.RAISE_SAFETY_STOCK),
    (("expedite",), ActionKind.EXPEDITE_PO),
    (("hold",), ActionKind.QUALITY_HOLD),
]
_DEFAULT_TEXT_KIND = ActionKind.QUALITY_HOLD


def _kind_from_text(line: str) -> ActionKind:
    """Map one free-text action line → an ActionKind via case-insensitive substring keywords."""
    low = (line or "").lower()
    for keywords, kind in _TEXT_KEYWORD_KINDS:
        if any(kw in low for kw in keywords):
            return kind
    return _DEFAULT_TEXT_KIND


def _planned_actions_from_text(actions: list[str], state: AgentState) -> list[PlannedAction]:
    """PURE deterministic fallback: synthesize structured `PlannedAction`s from the free-text
    `actions` lines so the Review page is NEVER empty when a recommendation has any actions.

    Each line → one action: kind from a keyword map (else QUALITY_HOLD), title=detail=line,
    qty=None, editable=False (a synthesized line carries no reviewable slider), unique key per
    index, target_table from `_KIND_TO_TABLE`. Empty input → []. No LLM/DB."""
    evidence_refs = _collect_citations(state)
    out: list[PlannedAction] = []
    for i, line in enumerate(actions or []):
        text = (line or "").strip()
        if not text:
            continue
        kind = _kind_from_text(text)
        key = f"{_slug(kind.value)}-{i}"
        out.append(
            PlannedAction(
                key=key,
                kind=kind,
                title=text,
                detail=text,
                target_table=_KIND_TO_TABLE[kind],  # type: ignore[arg-type]
                editable=False,
                qty=None,
                evidence_refs=evidence_refs,
                default_status="approve",
            )
        )
    return out


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


def _evidence_bundle(state: AgentState) -> dict:
    """A COMPACT, JSON-able bundle of the evidence the recommendation rests on, for the UI's
    evidence panel + the HITL interrupt payload (checkpointed, so caps mirror `_evidence_block`).
    Three keys: operational/analytics `data`, knowledge `rag`, recalled `memory`."""
    data: list[dict] = []
    if (or_ := state.get("operational_result")) and or_.rows:
        data += [
            {
                "source": "operational",
                "supplier_id": r.supplier_id,
                "sku": r.sku,
                "summary": r.summary,
                "similarity": r.similarity,
                "on_hand_qty": r.on_hand_qty,
                "open_po_qty": r.open_po_qty,
            }
            for r in or_.rows[:5]
        ]
    if (ar := state.get("analytics_result")) and ar.rows:
        data += [{"source": "analytics", **{k: v for k, v in row.items()}} for row in ar.rows[:8]]

    rag: list[dict] = []
    if (kr := state.get("knowledge_result")) and kr.passages:
        rag = [
            {"source": p.source, "content": (p.content or "")[:200], "score": p.score}
            for p in kr.passages[:4]
        ]

    # Objects (not bare strings) so the shape matches the frontend EvidenceBundle.memory type and
    # the /api/state/tables `recalled_memory` shape — the UI reads `.text`/`.score`.
    memory: list[dict] = []
    ctx = state.get("memory_context")
    if ctx and not ctx.is_empty:
        for bucket in (ctx.prior_approvals, ctx.preferences, ctx.supplier_notes):
            memory += [{"text": m.text, "score": m.score, "namespace": m.namespace} for m in bucket]
        memory = memory[:6]

    return {"data": data, "rag": rag, "memory": memory}


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
    top_row = None
    if (or_ := state.get("operational_result")) and or_.rows:
        counts.append(f"{len(or_.rows)} operational matches")
        top_row = or_.rows[0]

    # Even offline (USE_STUBS / no LLM), yield ONE deterministic structured action grounded in the
    # top operational row so the Meridian plan + write-back path stay exercisable end-to-end. The
    # hero data is a quality-defect scenario (Henkel cracking), so contain it with a quality hold.
    planned: list[_PlannedActionDraft] = []
    if top_row is not None:
        planned.append(
            _PlannedActionDraft(
                kind=ActionKind.QUALITY_HOLD,
                title=f"Quality hold on {top_row.sku or 'the matched SKU'}",
                detail=(
                    f"Hold the on-hand {top_row.sku or 'matched SKU'} units from "
                    f"{top_row.supplier_id or 'the supplier'} pending quality validation."
                ),
                qty=top_row.on_hand_qty,
                sku=top_row.sku,
                supplier_id=top_row.supplier_id,
                facts=[
                    f"on-hand: {top_row.on_hand_qty}",
                    f"open PO: {top_row.open_po_qty}",
                ],
            )
        )
    return _PlannerDraft(
        summary="Recommendation (deterministic compose) — " + (", ".join(counts) or "no gather results"),
        actions=["Review the matched operational cases and confirm scope before proceeding."],
        est_cost_usd=None,
        reasoning="LLM planner unavailable; composed from gather results.",
        planned_actions=planned,
    )


def planner_node(state: AgentState) -> dict:
    """Compose a recommendation via the planner LLM (with deterministic gate + citations)."""
    question = state.get("question", "")
    evidence = _evidence_block(state)
    memory = _memory_block(state)
    history = render_history(state)
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

    # Deterministically map the LLM's action drafts → durable PlannedActions (keys, target tables,
    # slider bounds, evidence_refs). RELIABLE FALLBACK: if the model returned only free-text
    # `actions` (no structured plan), synthesize structured actions from the text so the Review
    # page is never empty when a recommendation exists.
    planned_actions = _to_planned_actions(draft, state) or _planned_actions_from_text(draft.actions, state)
    # When a structured plan exists, the human-readable `actions` list mirrors its titles so chat
    # render + eval keep working; else keep the string fallback.
    if planned_actions:
        actions = [a.title for a in planned_actions]
    else:
        actions = draft.actions or ["Confirm scope with the planner before proceeding."]

    rec = PlannerRecommendation(
        summary=draft.summary,
        actions=actions,
        needs_approval=needs_approval,
        is_action_bearing=draft.is_action_bearing,
        est_cost_usd=draft.est_cost_usd,
        reasoning=draft.reasoning,
        citations=_collect_citations(state),
        planned_actions=planned_actions,
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
            # The structured Meridian plan the human reviews per-action, plus a compact evidence
            # bundle. Both are checkpointed at the interrupt boundary, so keep them small (the
            # plan caps at ≤4 actions and `_evidence_bundle` caps rows/passages like _evidence_block).
            "planned_actions": [a.model_dump() for a in rec.planned_actions] if rec else [],
            "evidence": _evidence_bundle(state),
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


def _coerce_action_decisions(raw) -> list[ActionDecision]:
    """Coerce the resume dict's `action_decisions` (a list of dicts or ActionDecisions) → models."""
    out: list[ActionDecision] = []
    for item in raw or []:
        if isinstance(item, ActionDecision):
            out.append(item)
        elif isinstance(item, dict) and item.get("key"):
            try:
                out.append(ActionDecision(**item))
            except Exception:  # noqa: BLE001 — skip a malformed per-action entry, keep the rest
                continue
    return out


def _coerce_decision(resume_value, user_id: str) -> HITLDecision:
    if isinstance(resume_value, HITLDecision):
        return resume_value
    if isinstance(resume_value, dict):
        return HITLDecision(
            verdict=HITLVerdict(resume_value.get("verdict", "approved")),
            note=resume_value.get("note"),
            user_id=resume_value.get("user_id") or user_id,
            rationale=resume_value.get("rationale"),
            action_decisions=_coerce_action_decisions(resume_value.get("action_decisions")),
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

    # Meridian write-back: an APPROVED action-bearing decision writes REAL structured rows to the
    # Lakebase write-back tables (approved_actions / planning_parameters / constraints). Best-effort
    # — a write failure logs + returns an error ledger but NEVER fails the run (commit must finalize).
    rec = state.get("recommendation")
    ledger: dict | None = None
    approved = bool(decision and decision.verdict == HITLVerdict.APPROVED)
    if approved and rec and rec.planned_actions:
        from agent_server.operational_db import write_committed_actions

        decisions_by_key = {d.key: d for d in decision.action_decisions}
        user_id = state.get("user_id", "unknown")
        try:
            ledger = await asyncio.to_thread(
                write_committed_actions,
                thread_id,
                user_id,
                decision.rationale,
                decisions_by_key,
                rec.planned_actions,
            )
        except Exception as exc:  # noqa: BLE001 — never fail the run on a write-back error
            logger.warning("Meridian write-back failed: %s", exc)
            ledger = {"error": str(exc), "counts": {}, "rows": {}}

    verdict = decision.verdict.value if decision else "n/a"
    written = ", ".join(f"{k}={v}" for k, v in counts.items()) if counts else "skipped"
    wb = ""
    if ledger is not None:
        wb = (
            ", writeback=error" if ledger.get("error")
            else ", writeback=" + ", ".join(f"{k}={v}" for k, v in (ledger.get("counts") or {}).items())
        )
    note = f"commit → finalized (verdict={verdict}, memory_writes={written}{wb})"
    notes = state.get("trace_notes", []) or []
    notes = [*notes, note]
    if w := _stream_writer():
        w({"kind": "trace", "note": note})
    out: dict = {"trace_notes": notes}
    if ledger is not None:
        out["commit_ledger"] = ledger
    return out
