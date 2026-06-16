"""Offline tests for long-term memory: curated write policy, recall, hydrate node, render.

No Lakebase needed — uses LangGraph's `InMemoryStore` as a drop-in BaseStore so the full
write → recall → hydrate plumbing runs locally.
"""

from __future__ import annotations

import asyncio

from langgraph.store.memory import InMemoryStore

from agent_server.contracts import (
    HITLDecision,
    HITLVerdict,
    MemoryContext,
    MemoryItem,
    OperationalResult,
    OperationalRow,
    PlannerRecommendation,
)
from agent_server.graph.memory_nodes import hydrate_memory_node
from agent_server.graph.planner import _memory_block
from agent_server.memory import (
    EMBED_INDEX,
    MEMORY_TEXT_FIELD,
    approvals_ns,
    build_memory_writes,
    preferences_ns,
    recall_approvals,
    recall_preferences,
    recall_supplier_notes,
    supplier_notes_ns,
    write_memories,
)

THREAD = "thread-xyz"
USER = "demo-user@databricks.com"


def _rec(action_bearing: bool = True) -> PlannerRecommendation:
    return PlannerRecommendation(
        summary="Quarantine the SKU-1001 batch and hold the open PO.",
        actions=["Quarantine lot", "Hold PO", "Notify supplier"],
        needs_approval=action_bearing,
        is_action_bearing=action_bearing,
        est_cost_usd=7980.0,
        reasoning="Recurring adhesive cracking on SKU-1001.",
    )


def _op_result(*supplier_ids: str) -> OperationalResult:
    rows = [
        OperationalRow(sku="SKU-1001", supplier_id=sid, summary="adhesive cracking", similarity=0.78)
        for sid in supplier_ids
    ]
    return OperationalResult(question="q", sql="SELECT 1", rows=rows)


def _state(rec=None, op=None, question="Henkel SKU-1001 adhesive cracking — mitigate?"):
    s = {"question": question, "user_id": USER}
    if rec is not None:
        s["recommendation"] = rec
    if op is not None:
        s["operational_result"] = op
    return s


# ── Write policy ──────────────────────────────────────────────────────────────────────────

def test_approved_action_bearing_writes_all_three_types():
    state = _state(rec=_rec(True), op=_op_result("SUP-001", "SUP-001", "SUP-014"))
    decision = HITLDecision(verdict=HITLVerdict.APPROVED, note="looks good", user_id=USER)
    writes = build_memory_writes(state, decision, THREAD)

    by_type = {w.namespace[0]: [] for w in writes}
    for w in writes:
        by_type.setdefault(w.namespace[0], []).append(w)

    assert len(by_type["approvals"]) == 1
    assert len(by_type["preferences"]) == 1
    # distinct suppliers only → SUP-001 + SUP-014 (the duplicate SUP-001 collapses)
    assert len(by_type["supplier_notes"]) == 2
    sids = {w.namespace[1] for w in by_type["supplier_notes"]}
    assert sids == {"SUP-001", "SUP-014"}

    # Every write embeds only the curated memory_text.
    for w in writes:
        assert w.index == EMBED_INDEX
        assert w.value.get(MEMORY_TEXT_FIELD)

    assert by_type["approvals"][0].namespace == approvals_ns(USER)
    assert by_type["preferences"][0].namespace == preferences_ns(USER)
    assert supplier_notes_ns("SUP-001") in {w.namespace for w in by_type["supplier_notes"]}


def test_rejected_writes_audit_only():
    state = _state(rec=_rec(True), op=_op_result("SUP-001"))
    decision = HITLDecision(verdict=HITLVerdict.REJECTED, note="too costly", user_id=USER)
    writes = build_memory_writes(state, decision, THREAD)
    types = [w.namespace[0] for w in writes]
    assert types == ["approvals"]
    assert writes[0].value["verdict"] == "rejected"


def test_approved_but_informational_writes_audit_only():
    state = _state(rec=_rec(False), op=_op_result("SUP-001"))
    decision = HITLDecision(verdict=HITLVerdict.APPROVED, user_id=USER)
    writes = build_memory_writes(state, decision, THREAD)
    assert [w.namespace[0] for w in writes] == ["approvals"]


def test_no_decision_still_audits():
    state = _state(rec=_rec(True), op=_op_result("SUP-001"))
    writes = build_memory_writes(state, None, THREAD)
    assert [w.namespace[0] for w in writes] == ["approvals"]
    assert writes[0].value["verdict"] is None


# ── Render ──────────────────────────────────────────────────────────────────────────────────

def test_memory_block_empty_is_blank():
    assert _memory_block({}) == ""
    assert _memory_block({"memory_context": MemoryContext()}) == ""


def test_memory_block_renders_sections():
    ctx = MemoryContext(
        preferences=[MemoryItem(text="Approved approach: quarantine")],
        prior_approvals=[MemoryItem(text="q1 → approved: hold PO")],
        supplier_notes=[MemoryItem(text="SUP-001/SKU-1001: quarantine — approved")],
    )
    block = _memory_block({"memory_context": ctx})
    assert "Planner preferences" in block
    assert "Relevant past decisions" in block
    assert "Known supplier notes" in block
    assert "quarantine" in block


# ── Round-trip: write → recall → hydrate (InMemoryStore) ─────────────────────────────────────

def test_write_then_recall_roundtrip():
    async def run():
        store = InMemoryStore()
        state = _state(rec=_rec(True), op=_op_result("SUP-001"))
        decision = HITLDecision(verdict=HITLVerdict.APPROVED, note="ok", user_id=USER)
        counts = await write_memories(store, build_memory_writes(state, decision, THREAD))
        assert counts.get("approvals") == 1
        assert counts.get("preferences") == 1
        assert counts.get("supplier_notes") == 1

        prefs = await recall_preferences(store, USER, "adhesive", limit=3, threshold=None)
        approvals = await recall_approvals(store, USER, "adhesive", limit=3, threshold=None)
        notes = await recall_supplier_notes(store, ["SUP-001"], "adhesive", limit=3, threshold=None)
        assert prefs and "Approved approach" in prefs[0].text
        assert approvals and "approved" in approvals[0].text
        assert notes and notes[0].text.startswith("SUP-001/")

    asyncio.run(run())


def test_hydrate_node_populates_context():
    async def run():
        store = InMemoryStore()
        state = _state(rec=_rec(True), op=_op_result("SUP-001"))
        decision = HITLDecision(verdict=HITLVerdict.APPROVED, note="ok", user_id=USER)
        await write_memories(store, build_memory_writes(state, decision, THREAD))

        # New turn: hydrate reads the store given the same user + the surfaced supplier.
        turn2 = _state(op=_op_result("SUP-001"), question="What about SKU-1001 again?")
        config = {"configurable": {"store": store, "thread_id": "thread-2"}}
        out = await hydrate_memory_node(turn2, config)
        ctx = out["memory_context"]
        assert isinstance(ctx, MemoryContext)
        assert not ctx.is_empty
        assert any("hydrate_memory →" in n for n in out["trace_notes"])

    asyncio.run(run())


def test_hydrate_node_no_store_is_safe():
    async def run():
        out = await hydrate_memory_node(_state(), {"configurable": {}})
        assert out["memory_context"].is_empty
        assert any("skipped (no store)" in n for n in out["trace_notes"])

    asyncio.run(run())
