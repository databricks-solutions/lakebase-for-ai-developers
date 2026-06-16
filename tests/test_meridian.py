"""Offline tests for the Meridian structured-plan + write-back seams.

No Lakebase / LLM needed — every covered function is a PURE seam (draft → PlannedAction,
PlannedAction + decisions → write-back rows, resume dict → HITLDecision, state → evidence
bundle), plus a serde registration sanity check. Mirrors `tests/test_memory.py`: plain pytest
functions, `asyncio.run` for the async path, no network.
"""

from __future__ import annotations

from agent_server import lakebase as lakebase_mod
from agent_server.agent import _custom_outputs
from agent_server.contracts import (
    ActionDecision,
    ActionFact,
    ActionKind,
    HITLDecision,
    HITLVerdict,
    KnowledgePassage,
    KnowledgeResult,
    MemoryContext,
    MemoryItem,
    OperationalResult,
    OperationalRow,
    PlannedAction,
    PlannerRecommendation,
)
from agent_server.graph.planner import (
    _PlannedActionDraft,
    _PlannerDraft,
    _coerce_decision,
    _evidence_bundle,
    _fallback_draft,
    _planned_actions_from_text,
    _to_planned_actions,
    planner_node,
)
from agent_server.operational_db import build_writeback_rows

THREAD = "thread-meridian"
USER = "demo-user@databricks.com"


# ── Shared fixtures (plain helpers, no pytest fixtures to match test_memory.py) ──────────────

def _op_result() -> OperationalResult:
    rows = [
        OperationalRow(
            sku="SKU-1001", supplier_id="SUP-001", summary="adhesive cracking",
            similarity=0.82, on_hand_qty=40, open_po_qty=800,
        )
    ]
    return OperationalResult(question="q", sql="SELECT 1", rows=rows)


def _draft_with_actions() -> _PlannerDraft:
    return _PlannerDraft(
        summary="Mitigate the SKU-1001 coverage gap.",
        actions=["legacy string fallback"],
        is_action_bearing=True,
        est_cost_usd=12000.0,
        reasoning="Recurring Henkel adhesive cracking; 40 on-hand vs 760 gap.",
        planned_actions=[
            _PlannedActionDraft(
                kind=ActionKind.EXPEDITE_PO, title="Expedite open PO PO-2026-0042",
                detail="Pull in the Henkel open PO to cover the gap.",
                qty=800, cost_delta=5000, sku="SKU-1001", supplier_id="SUP-001",
                po_id="PO-2026-0042", facts=["on-hand: 40 units", "gap: 760 units"],
            ),
            _PlannedActionDraft(
                kind=ActionKind.SPLIT_SOURCE, title="Buffer order from DuPont",
                detail="Place a 200-unit buffer order from the alternate supplier.",
                qty=200, sku="SKU-1001", supplier_id="SUP-002",
            ),
            _PlannedActionDraft(
                kind=ActionKind.RAISE_SAFETY_STOCK, title="Raise SKU-1001 safety stock",
                detail="Bump safety stock to 500 units.",
                qty=500, sku="SKU-1001",
            ),
            _PlannedActionDraft(
                kind=ActionKind.ALLOCATION_CONSTRAINT, title="Prioritize Program Helios",
                detail="Allocate constrained SKU-1001 to Program Helios first.",
                sku="SKU-1001", program="Helios",
            ),
        ],
    )


def _state(rec=None):
    s = {"question": "Henkel SKU-1001 cracking — mitigate?", "user_id": USER,
         "operational_result": _op_result()}
    if rec is not None:
        s["recommendation"] = rec
    return s


# ── _to_planned_actions: drafts → PlannedAction mapping ──────────────────────────────────────

def test_to_planned_actions_maps_target_tables_and_keys():
    draft = _draft_with_actions()
    actions = _to_planned_actions(draft, _state())
    assert len(actions) == 4

    by_kind = {a.kind: a for a in actions}
    assert by_kind[ActionKind.EXPEDITE_PO].target_table == "approved_actions"
    assert by_kind[ActionKind.SPLIT_SOURCE].target_table == "approved_actions"
    assert by_kind[ActionKind.RAISE_SAFETY_STOCK].target_table == "planning_parameters"
    assert by_kind[ActionKind.ALLOCATION_CONSTRAINT].target_table == "constraints"

    # Stable, slugified key incorporating the most specific id (po_id for expedite).
    assert by_kind[ActionKind.EXPEDITE_PO].key == "expedite-po-po-2026-0042"
    # Deterministic: same draft → same key.
    assert _to_planned_actions(draft, _state())[0].key == actions[0].key


def test_to_planned_actions_keys_unique_on_collision():
    """Two actions of the same kind+identifier must NOT share a key. Otherwise they collapse to one
    row on commit (the write-back PKs are keyed by it), so N actions silently become 1. Regression
    for the live-integration finding where two allocation constraints on SKU-1001 clobbered."""
    draft = _PlannerDraft(
        summary="Two quality holds on the same SKU.",
        actions=["hold A", "hold B"],
        is_action_bearing=True,
        planned_actions=[
            _PlannedActionDraft(kind=ActionKind.ALLOCATION_CONSTRAINT, title="Hold lot A",
                                detail="Quality hold on lot A.", sku="SKU-1001"),
            _PlannedActionDraft(kind=ActionKind.ALLOCATION_CONSTRAINT, title="Hold lot B",
                                detail="Quality hold on lot B.", sku="SKU-1001"),
        ],
    )
    keys = [a.key for a in _to_planned_actions(draft, _state())]
    assert len(keys) == 2 and len(set(keys)) == 2, f"keys must be unique, got {keys}"


def test_to_planned_actions_safety_stock_slider_bounds():
    draft = _draft_with_actions()
    actions = _to_planned_actions(draft, _state())
    ss = next(a for a in actions if a.kind == ActionKind.RAISE_SAFETY_STOCK)
    # Floor is the SKU's current on-hand (40) from the operational rows; ceiling ~2000, step 10.
    assert ss.qty_min == 40.0
    assert ss.qty_max == 2000.0
    assert ss.qty_step == 10.0
    assert ss.qty_label


def test_to_planned_actions_facts_and_evidence_refs():
    draft = _draft_with_actions()
    actions = _to_planned_actions(draft, _state())
    expedite = next(a for a in actions if a.kind == ActionKind.EXPEDITE_PO)
    # facts strings split on ':' into k/v ActionFacts.
    assert all(isinstance(f, ActionFact) for f in expedite.facts)
    facts = {f.k: f.v for f in expedite.facts}
    assert facts["on-hand"] == "40 units"
    assert facts["gap"] == "760 units"
    # evidence_refs reuse _collect_citations (operational-sql ref present).
    assert any(ref.startswith("operational-sql:") for ref in expedite.evidence_refs)
    assert expedite.default_status == "approve"


def test_planner_node_actions_mirror_titles_via_helper():
    # When planned_actions exist, rec.actions should be the titles (chat render + eval still work).
    draft = _draft_with_actions()
    actions = _to_planned_actions(draft, _state())
    assert [a.title for a in actions] == [d.title for d in draft.planned_actions]


def test_fallback_draft_yields_one_structured_action_offline():
    # USE_STUBS / offline: the deterministic fallback still produces a structured plan. The hero
    # data is a quality scenario, so the deterministic action is a QUALITY_HOLD on the on-hand units.
    draft = _fallback_draft(_state(), "mitigate?")
    assert len(draft.planned_actions) == 1
    assert draft.planned_actions[0].kind == ActionKind.QUALITY_HOLD
    actions = _to_planned_actions(draft, _state())
    assert actions and actions[0].target_table == "approved_actions"
    assert actions[0].sku == "SKU-1001"
    assert actions[0].qty == 40.0  # holds the on-hand units from the operational rows


# ── Quality-containment kinds (Meridian quality pivot) ────────────────────────────────────────

def _quality_draft() -> _PlannerDraft:
    return _PlannerDraft(
        summary="Contain the Henkel SKU-1001 cracking.",
        actions=["legacy string fallback"],
        is_action_bearing=True,
        est_cost_usd=8000.0,
        reasoning="Recurring Henkel adhesive cracking on SKU-1001.",
        planned_actions=[
            _PlannedActionDraft(
                kind=ActionKind.QUALITY_HOLD, title="Hold on-hand SKU-1001",
                detail="Hold the 40 on-hand units pending validation.",
                sku="SKU-1001", supplier_id="SUP-001",
                facts=["on-hand: 40 units"],
            ),
            _PlannedActionDraft(
                kind=ActionKind.QUARANTINE_PO, title="Quarantine incoming PO",
                detail="Quarantine the 500-unit incoming PO.",
                qty=500, sku="SKU-1001", supplier_id="SUP-001", po_id="PO-2026-0099",
            ),
            _PlannedActionDraft(
                kind=ActionKind.TIGHTEN_INSPECTION, title="Tighten inspection",
                detail="Raise SKU-1001 inspection level to 100%.",
                qty=100, sku="SKU-1001",
            ),
            _PlannedActionDraft(
                kind=ActionKind.SUPPLIER_QUALITY_HOLD, title="Hold SUP-001/SKU-1001",
                detail="No new SUP-001 SKU-1001 acceptance until validated.",
                sku="SKU-1001", supplier_id="SUP-001",
                facts=["scope: SKU-1001", "until: validated"],
            ),
        ],
    )


def test_to_planned_actions_maps_new_quality_kinds_to_tables():
    actions = _to_planned_actions(_quality_draft(), _state())
    by_kind = {a.kind: a for a in actions}
    assert by_kind[ActionKind.QUALITY_HOLD].target_table == "approved_actions"
    assert by_kind[ActionKind.QUARANTINE_PO].target_table == "approved_actions"
    assert by_kind[ActionKind.TIGHTEN_INSPECTION].target_table == "planning_parameters"
    assert by_kind[ActionKind.SUPPLIER_QUALITY_HOLD].target_table == "constraints"


def test_to_planned_actions_quality_kind_slider_bounds_and_editability():
    actions = _to_planned_actions(_quality_draft(), _state())
    by_kind = {a.kind: a for a in actions}

    hold = by_kind[ActionKind.QUALITY_HOLD]
    assert hold.editable is True
    assert hold.qty_label == "Units to hold"
    assert hold.qty_min == 0.0
    assert hold.qty_step == 10.0
    # No LLM qty given → defaults to on-hand from the operational rows.
    assert hold.qty == 40.0

    quarantine = by_kind[ActionKind.QUARANTINE_PO]
    assert quarantine.editable is True
    assert quarantine.qty_label == "PO units"
    assert quarantine.qty_min == 0.0
    assert quarantine.qty_step == 50.0

    inspection = by_kind[ActionKind.TIGHTEN_INSPECTION]
    assert inspection.editable is True
    assert inspection.qty_label == "Inspection %"
    assert inspection.qty_min == 0.0
    assert inspection.qty_max == 100.0
    assert inspection.qty_step == 5.0

    supplier_hold = by_kind[ActionKind.SUPPLIER_QUALITY_HOLD]
    assert supplier_hold.editable is False  # it's a rule, not a quantity edit


# ── _planned_actions_from_text: deterministic free-text → structured fallback ────────────────

def test_planned_actions_from_text_maps_keywords_to_kinds_and_tables():
    actions = _planned_actions_from_text(
        ["Quarantine incoming PO", "Quality hold on 40 units", "Tighten inspection to 100%"],
        _state(),
    )
    assert len(actions) == 3
    assert actions[0].kind == ActionKind.QUARANTINE_PO
    assert actions[0].target_table == "approved_actions"
    assert actions[1].kind == ActionKind.QUALITY_HOLD
    assert actions[1].target_table == "approved_actions"
    assert actions[2].kind == ActionKind.TIGHTEN_INSPECTION
    assert actions[2].target_table == "planning_parameters"
    # Synthesized lines: title == detail == the text line, no qty, not editable, unique keys.
    assert actions[0].title == actions[0].detail == "Quarantine incoming PO"
    assert all(a.qty is None and a.editable is False for a in actions)
    assert len({a.key for a in actions}) == 3


def test_planned_actions_from_text_default_kind_and_empty():
    # No keyword → default QUALITY_HOLD; empty / blank input → [].
    fallback = _planned_actions_from_text(["Do something unmapped"], _state())
    assert fallback and fallback[0].kind == ActionKind.QUALITY_HOLD
    assert _planned_actions_from_text([], _state()) == []
    assert _planned_actions_from_text(["", "   "], _state()) == []


def test_planned_actions_from_text_keyword_branches():
    cases = {
        "Expedite the open PO": ActionKind.EXPEDITE_PO,
        "Split source to DuPont as a bridge": ActionKind.SPLIT_SOURCE,
        "Place a bridge order from the alternate": ActionKind.SPLIT_SOURCE,
        "Raise safety stock to 500": ActionKind.RAISE_SAFETY_STOCK,
        "Hold the lot": ActionKind.QUALITY_HOLD,
    }
    for text, expected in cases.items():
        got = _planned_actions_from_text([text], _state())
        assert got[0].kind == expected, f"{text!r} → {got[0].kind} (want {expected})"


# ── planner_node: Review-never-empty guarantee ───────────────────────────────────────────────

def test_planner_node_fallback_guarantees_non_empty_planned_actions():
    """A draft that returns only free-text `actions` (no structured `planned_actions`) must still
    yield a recommendation with a NON-EMPTY structured plan — the Review-never-empty guarantee.
    We patch `_llm_draft` (no live endpoint) to return that degenerate shape deterministically."""
    from unittest.mock import patch

    degenerate = _PlannerDraft(
        summary="Contain the Henkel cracking.",
        actions=["Quarantine incoming PO", "Quality hold on 40 units", "Tighten inspection"],
        is_action_bearing=True,
        est_cost_usd=8000.0,
        reasoning="Free-text only; structured plan empty.",
        planned_actions=[],  # the bug: model filled free-text but left planned_actions empty
    )
    with patch("agent_server.graph.planner._llm_draft", return_value=degenerate):
        out = planner_node(_state())

    rec = out["recommendation"]
    assert rec.planned_actions, "Review must never be empty when a recommendation has actions"
    kinds = [a.kind for a in rec.planned_actions]
    assert ActionKind.QUARANTINE_PO in kinds
    assert ActionKind.QUALITY_HOLD in kinds
    assert ActionKind.TIGHTEN_INSPECTION in kinds
    # rec.actions mirrors the synthesized plan titles.
    assert rec.actions == [a.title for a in rec.planned_actions]


def test_planner_node_offline_fallback_yields_quality_plan():
    """USE_STUBS / offline smoke: with no LLM (the real `_llm_draft` returns None against no
    endpoint), planner_node falls to `_fallback_draft` and still returns a populated quality plan."""
    from unittest.mock import patch

    # Force the no-LLM path deterministically (don't depend on a network failure for the smoke).
    with patch("agent_server.graph.planner._llm_draft", return_value=None):
        out = planner_node(_state())

    rec = out["recommendation"]
    assert rec.planned_actions, "offline fallback must still populate planned_actions"
    assert rec.planned_actions[0].kind == ActionKind.QUALITY_HOLD
    assert rec.planned_actions[0].target_table == "approved_actions"


# ── build_writeback_rows: planned actions + decisions → per-table rows ────────────────────────

def _planned() -> list[PlannedAction]:
    return _to_planned_actions(_draft_with_actions(), _state())


def test_build_writeback_rows_routes_per_table():
    rows = build_writeback_rows(THREAD, USER, "because gap", {}, _planned())
    # expedite + split-source → approved_actions; safety stock → planning_parameters; alloc → constraints.
    assert len(rows["approved_actions"]) == 2
    assert len(rows["planning_parameters"]) == 1
    assert len(rows["constraints"]) == 1
    pp = rows["planning_parameters"][0]
    assert pp["sku"] == "SKU-1001"
    assert pp["parameter"] == "safety_stock"
    assert pp["new_value"] == 500  # the draft qty becomes new_value by default
    assert pp["rationale"] == "because gap"


def test_build_writeback_rows_edited_qty_and_safety_override():
    planned = _planned()
    expedite_key = next(a.key for a in planned if a.kind == ActionKind.EXPEDITE_PO)
    ss_key = next(a.key for a in planned if a.kind == ActionKind.RAISE_SAFETY_STOCK)
    decisions = {
        expedite_key: ActionDecision(key=expedite_key, status="approve", edited_qty=600),
        ss_key: ActionDecision(key=ss_key, status="approve", safety_stock_override=750),
    }
    rows = build_writeback_rows(THREAD, USER, "r", decisions, planned)
    expedite_row = next(r for r in rows["approved_actions"] if r["kind"] == "expedite_po")
    assert expedite_row["qty"] == 600  # edited_qty overrides the proposed qty
    assert rows["planning_parameters"][0]["new_value"] == 750  # override wins


def test_build_writeback_rows_held_action_excluded_from_structural_table():
    planned = _planned()
    ss_key = next(a.key for a in planned if a.kind == ActionKind.RAISE_SAFETY_STOCK)
    decisions = {ss_key: ActionDecision(key=ss_key, status="hold")}
    rows = build_writeback_rows(THREAD, USER, "r", decisions, planned)
    # Held safety-stock change does NOT land in planning_parameters…
    assert rows["planning_parameters"] == []
    # …but is audited as a hold row in approved_actions.
    held = [r for r in rows["approved_actions"] if r["status"] == "hold"]
    assert held and held[0]["kind"] == "raise_safety_stock"


def test_build_writeback_rows_quality_kinds_parameter_and_constraint_kind():
    """Quality plan → the three tables with the right per-kind column values: quality_hold +
    quarantine_po → approved_actions; tighten_inspection → planning_parameters.parameter=
    'inspection_level'; supplier_quality_hold → constraints.kind='supplier_hold'."""
    planned = _to_planned_actions(_quality_draft(), _state())
    rows = build_writeback_rows(THREAD, USER, "validate first", {}, planned)

    # quality_hold + quarantine_po both land in approved_actions with their ActionKind value.
    aa_kinds = {r["kind"] for r in rows["approved_actions"]}
    assert {"quality_hold", "quarantine_po"} <= aa_kinds

    # tighten_inspection → planning_parameters with parameter='inspection_level'.
    assert len(rows["planning_parameters"]) == 1
    pp = rows["planning_parameters"][0]
    assert pp["parameter"] == "inspection_level"
    assert pp["new_value"] == 100  # the draft qty becomes new_value by default
    assert pp["sku"] == "SKU-1001"

    # supplier_quality_hold → constraints with kind='supplier_hold'.
    assert len(rows["constraints"]) == 1
    assert rows["constraints"][0]["kind"] == "supplier_hold"


def test_build_writeback_rows_safety_stock_parameter_unchanged():
    # raise_safety_stock still derives parameter='safety_stock' (back-compat for the shortage plan).
    rows = build_writeback_rows(THREAD, USER, "gap", {}, _planned())
    assert rows["planning_parameters"][0]["parameter"] == "safety_stock"
    # allocation_constraint still derives constraint kind='allocation'.
    assert rows["constraints"][0]["kind"] == "allocation"


# ── _coerce_decision: resume dict → HITLDecision ─────────────────────────────────────────────

def test_coerce_decision_parses_rationale_and_action_decisions():
    resume = {
        "verdict": "approved",
        "user_id": USER,
        "rationale": "Coverage gap is material; expedite + buffer.",
        "action_decisions": [
            {"key": "expedite-po-po-2026-0042", "status": "approve", "edited_qty": 600},
            {"key": "raise-safety-stock-sku-1001", "status": "hold"},
        ],
    }
    d = _coerce_decision(resume, "fallback-user")
    assert d.verdict == HITLVerdict.APPROVED
    assert d.user_id == USER
    assert d.rationale.startswith("Coverage gap")
    assert len(d.action_decisions) == 2
    assert all(isinstance(a, ActionDecision) for a in d.action_decisions)
    assert d.action_decisions[0].edited_qty == 600
    assert d.action_decisions[1].status == "hold"


def test_coerce_decision_bare_string_fallback_still_works():
    d = _coerce_decision("approved", USER)
    assert d.verdict == HITLVerdict.APPROVED
    assert d.user_id == USER
    assert d.action_decisions == []


def test_coerce_decision_dict_without_meridian_fields():
    d = _coerce_decision({"verdict": "rejected", "note": "too costly"}, USER)
    assert d.verdict == HITLVerdict.REJECTED
    assert d.note == "too costly"
    assert d.rationale is None
    assert d.action_decisions == []


# ── _evidence_bundle + _custom_outputs ───────────────────────────────────────────────────────

def test_evidence_bundle_three_keys():
    state = {
        "operational_result": _op_result(),
        "knowledge_result": KnowledgeResult(
            query="q",
            passages=[KnowledgePassage(
                chunk_id="c1", source="contract.pdf", doc_type="contract", content="penalty clause text",
            )],
        ),
        "memory_context": MemoryContext(
            prior_approvals=[MemoryItem(text="q → approved: hold the PO")],
        ),
    }
    bundle = _evidence_bundle(state)
    assert set(bundle.keys()) == {"data", "rag", "memory"}
    assert bundle["data"] and bundle["data"][0]["sku"] == "SKU-1001"
    # rag/memory are OBJECTS matching the frontend EvidenceBundle type (the UI reads `.content`/
    # `.text`) — bare strings here white-screen ReviewPanel (truncate(undefined)). Regression guard.
    assert bundle["rag"] and bundle["rag"][0]["source"] == "contract.pdf"
    assert bundle["rag"][0]["content"] == "penalty clause text"
    assert bundle["memory"] and bundle["memory"][0]["text"] == "q → approved: hold the PO"
    assert "score" in bundle["memory"][0] and "namespace" in bundle["memory"][0]


def test_evidence_bundle_empty_state_is_three_empty_lists():
    bundle = _evidence_bundle({})
    assert bundle == {"data": [], "rag": [], "memory": []}


def test_custom_outputs_carries_planned_actions_evidence_and_ledger():
    rec = PlannerRecommendation(
        summary="Mitigate the gap.",
        actions=["Expedite open PO PO-2026-0042"],
        needs_approval=True,
        is_action_bearing=True,
        planned_actions=_planned(),
    )
    ledger = {"counts": {"approved_actions": 2, "planning_parameters": 1, "constraints": 1}, "rows": {}}
    state = {
        "recommendation": rec,
        "operational_result": _op_result(),
        "commit_ledger": ledger,
    }
    out = _custom_outputs(state, interrupt_payload=None)
    # recommendation carries the structured plan.
    assert out["recommendation"]["planned_actions"]
    assert len(out["recommendation"]["planned_actions"]) == 4
    # 3-key evidence bundle present.
    assert set(out["evidence"].keys()) == {"data", "rag", "memory"}
    # commit_ledger passes through.
    assert out["commit_ledger"] is ledger
    assert out["status"] == "completed"


# ── Serde registration sanity ────────────────────────────────────────────────────────────────

def test_meridian_contract_types_registered_for_checkpoint():
    from agent_server import contracts

    for t in (
        contracts.PlannedAction,
        contracts.ActionKind,
        contracts.ActionFact,
        contracts.ActionDecision,
    ):
        assert t in lakebase_mod._CHECKPOINT_CONTRACT_TYPES


def test_planner_recommendation_and_decision_roundtrip_through_serde():
    # Round-trip the new shapes through the contract-aware serializer used on the checkpoint.
    serde = lakebase_mod._contract_aware_serde()

    rec = PlannerRecommendation(
        summary="Mitigate the gap.", actions=["Expedite"], needs_approval=True,
        is_action_bearing=True, planned_actions=_planned(),
    )
    decision = HITLDecision(
        verdict=HITLVerdict.APPROVED, user_id=USER, rationale="material gap",
        action_decisions=[ActionDecision(key="expedite-po-po-2026-0042", edited_qty=600)],
    )
    for obj in (rec, decision):
        restored = serde.loads_typed(serde.dumps_typed(obj))
        assert type(restored) is type(obj)
    # spot-check fields survived
    restored_rec = serde.loads_typed(serde.dumps_typed(rec))
    assert restored_rec.planned_actions[0].kind == ActionKind.EXPEDITE_PO
    restored_dec = serde.loads_typed(serde.dumps_typed(decision))
    assert restored_dec.action_decisions[0].edited_qty == 600


# ── Write-back schema move: build_writeback_rows is a PURE seam (schema move is DB-side only) ──

def test_build_writeback_rows_unaffected_by_writeback_schema_move():
    """The write-back tables moved to the SP-owned `lakebase_writeback_schema` (out of `public`),
    but that's a DDL/connection concern — `build_writeback_rows` is pure row-shaping and must still
    key its output by the logical table names (approved_actions / planning_parameters / constraints),
    NOT by any schema-qualified name. Regression guard for the schema move."""
    rows = build_writeback_rows(THREAD, USER, "because gap", {}, _planned())
    assert set(rows.keys()) == {"approved_actions", "planning_parameters", "constraints"}
    # No schema qualification leaked into the row-dict keys.
    for table in rows:
        assert "." not in table
    # Row dicts carry plain (unqualified) column names — the schema lives only in the DDL/upsert SQL.
    assert all("sku" in r for r in rows["planning_parameters"])
    assert all("action_key" in r for r in rows["approved_actions"])


def test_config_three_lakebase_schemas_pairwise_distinct():
    """operational (`public`, synced reads) / memory (LangGraph-owned) / write-back (SP-owned app
    schema) must be three DIFFERENT schemas — collapsing any pair re-introduces the
    'permission denied for schema public' startup crash the move fixed."""
    from agent_server.config import settings

    schemas = {
        "operational": settings.lakebase_operational_schema,
        "memory": settings.lakebase_memory_schema,
        "writeback": settings.lakebase_writeback_schema,
    }
    assert all(schemas.values()), f"all three schemas must be set: {schemas}"
    assert len(set(schemas.values())) == 3, f"schemas must be pairwise distinct: {schemas}"
