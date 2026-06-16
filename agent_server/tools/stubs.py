"""In-memory fakes for every gather agent. Let the router graph build and run offline
without a workspace, a VS index, or a Genie space.

Each fake matches the shape of the real impl in contracts.py — so swapping `query_*_impl`
for `query_*_fake` is a one-line change in `gather_nodes.py`. Drive selection with the
`USE_STUBS=1` env var (consumed by `agent_server/graph/gather_nodes.py`).
"""

from __future__ import annotations

from agent_server.contracts import (
    DocType,
    GenieResult,
    KnowledgePassage,
    KnowledgeResult,
    OperationalResult,
    OperationalRow,
)


def query_knowledge_fake(
    query: str, doc_types: list[DocType] | None = None, k: int = 5
) -> KnowledgeResult:
    sample = [
        KnowledgePassage(
            chunk_id="stub-1",
            source="contracts/CTR-2024-1000_Caterpillar_Inc.pdf",
            page=2,
            doc_type=DocType.CONTRACT,
            doc_id="CTR-2024-1000",
            content=(
                "Caterpillar Inc. master supply agreement: Tier 1 pricing at $6.50/unit "
                "through Dec 2026; volume discount triggers at 50K units/quarter."
            ),
            score=0.91,
        ),
        KnowledgePassage(
            chunk_id="stub-2",
            source="supplier_notifications/Nucor_Steel_Corporation_Carbon_Steel_20260616.pdf",
            page=1,
            doc_type=DocType.SUPPLIER_NOTIFICATION,
            doc_id="Nucor_Steel_Corporation_Carbon_Steel_20260616",
            content=(
                "Nucor announces 7% price increase on carbon steel effective 2026-06-16 "
                "due to ore feedstock cost pressures."
            ),
            score=0.84,
        ),
    ]
    if doc_types:
        sample = [p for p in sample if p.doc_type in doc_types]
    return KnowledgeResult(query=query, passages=sample[:k])


def ask_genie_fake(question: str, conversation_id: str | None = None) -> GenieResult:
    return GenieResult(
        question=question,
        answer="Open PO quantity for Q4 totals 142,300 units across 12 suppliers.",
        sql=(
            "SELECT s.name, SUM(po.qty) AS open_qty "
            "FROM purchase_orders po JOIN suppliers s ON s.supplier_id = po.supplier_id "
            "WHERE po.status = 'open' AND po.expected_date BETWEEN '2026-10-01' AND '2026-12-31' "
            "GROUP BY s.name ORDER BY open_qty DESC"
        ),
        rows=[
            {"supplier": "Nucor Steel", "open_qty": 42500},
            {"supplier": "Henkel AG", "open_qty": 28800},
            {"supplier": "BASF Corp.", "open_qty": 19100},
        ],
        conversation_id=conversation_id or "stub-conv-1",
    )


def query_operational_fake(question: str) -> OperationalResult:
    return OperationalResult(
        question=question,
        sql="-- STUB: hybrid similarity + join",
        rows=[
            OperationalRow(
                sku="SKU-1001",
                supplier_id="SUP-014",
                summary="Quality issue: fastener brittleness, batch 2026-Q1.",
                similarity=0.88,
                on_hand_qty=320.0,
                open_po_qty=900.0,
            )
        ],
    )
