"""Certified Q→expected evaluation set for the Genie Analytics agent.

Expected values are DERIVED from `seeds.py` (not hardcoded), so they can never drift from the
generated data. Consumed by the MLflow `mlflow.genai` eval harness (P2) — each item pairs a
natural-language question with the deterministic literal the seeded data yields, so a Genie answer
can be asserted with a numeric/row-set scorer (plus an LLM faithfulness judge for the prose).

Each item: {id, question, expected, note}. `expected` is the ground-truth literal/structure.
"""

from __future__ import annotations

from data.operational import seeds


def _latest_supplier_status() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for r in seeds.build_supplier_status():
        sid = r["supplier_id"]
        if sid not in latest or r["last_updated"] > latest[sid]["last_updated"]:
            latest[sid] = r
    return latest


def _name(supplier_id: str) -> str:
    return next(s["name"] for s in seeds.SUPPLIERS if s["supplier_id"] == supplier_id)


def _at_risk_ranked() -> list[dict]:
    latest = _latest_supplier_status()
    at_risk = [
        {"supplier": _name(sid), "risk_score": r["risk_score"]}
        for sid, r in latest.items() if r["status"] == "at_risk"
    ]
    return sorted(at_risk, key=lambda x: x["risk_score"], reverse=True)


def _sku1001_open_po_total() -> float:
    return sum(po["qty"] for po in seeds.build_purchase_orders()
               if po["sku"] == seeds.HERO_SKU and po["status"] == "open")


def _sku1001_on_hand() -> float:
    return next(r["on_hand_qty"] for r in seeds.build_inventory_current() if r["sku"] == seeds.HERO_SKU)


def _q4_open_po_by_supplier() -> dict[str, float]:
    agg: dict[str, float] = {}
    for po in seeds.build_purchase_orders():
        d = po["expected_date"]
        if po["status"] == "open" and d.year == 2026 and d.month in (10, 11, 12):
            agg[_name(po["supplier_id"])] = agg.get(_name(po["supplier_id"]), 0.0) + po["qty"]
    return dict(sorted(agg.items(), key=lambda kv: kv[1], reverse=True))


def _adhesives_suppliers() -> list[str]:
    return sorted(_name(sid) for sid in seeds.suppliers_by_category("adhesives"))


EVAL_SET = [
    {
        "id": "EVAL-1",
        "question": "Which suppliers are at risk, ranked by risk score (highest first)?",
        "expected": _at_risk_ranked(),
        "note": "Latest supplier_status per supplier; expect Henkel AG (SUP-001) at 82 at the top.",
    },
    {
        "id": "EVAL-2",
        "question": "What is the current on-hand inventory for SKU-1001?",
        "expected": _sku1001_on_hand(),
        "note": "Latest row per (sku, location) summed; the hero coverage gap = 40.",
    },
    {
        "id": "EVAL-3",
        "question": "What is the total open purchase-order quantity for SKU-1001?",
        "expected": _sku1001_open_po_total(),
        "note": "Henkel 500 + DuPont 300 = 800 (status='open').",
    },
    {
        "id": "EVAL-4",
        "question": "For SKU-1001, what is the coverage gap (open POs minus on-hand)?",
        "expected": _sku1001_open_po_total() - _sku1001_on_hand(),
        "note": "800 - 40 = 760.",
    },
    {
        "id": "EVAL-5",
        "question": "Which suppliers can supply adhesives?",
        "expected": _adhesives_suppliers(),
        "note": "suppliers.categories LIKE '%adhesives%' — proves dual-sourcing is answerable.",
    },
    {
        "id": "EVAL-6",
        "question": "What is the total open PO quantity by supplier for Q4 2026?",
        "expected": _q4_open_po_by_supplier(),
        "note": "status='open' AND expected_date in 2026-10-01..12-31, grouped by supplier name.",
    },
]


if __name__ == "__main__":
    import json

    for item in EVAL_SET:
        print(f"{item['id']}: {item['question']}")
        print(f"   expected: {json.dumps(item['expected'], default=str)}")
        print(f"   note    : {item['note']}\n")
