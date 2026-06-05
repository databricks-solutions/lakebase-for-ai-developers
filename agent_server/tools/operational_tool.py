"""Operational agent tool — STUB.

This stub returns a canned shape so the router graph compiles and runs end-to-end while
WS2 (Chandhana) builds the real Lakebase hybrid query (similarity + JOIN to on-hand /
open-POs in one SQL). The real implementation must produce the same `OperationalResult`
shape so swapping it in is a no-op for the supervisor.
"""

from __future__ import annotations

from langchain_core.tools import tool

from agent_server.contracts import OperationalResult, OperationalRow


_STUB_SQL = """\
-- WS2 STUB. Real query will join agent_memory (with embedding similarity) to
-- inventory + open_pos + user_access in one Lakebase SQL statement.
SELECT 'STUB' AS sku, 'WS2-not-implemented' AS summary;
"""


def query_operational_impl(question: str, user_id: str) -> OperationalResult:
    """Stub: returns one placeholder row + the stub SQL so traces are coherent."""
    return OperationalResult(
        question=question,
        sql=_STUB_SQL,
        rows=[
            OperationalRow(
                sku="STUB-SKU",
                summary="Operational agent not yet wired — WS2 in progress.",
                similarity=None,
                on_hand_qty=None,
                open_po_qty=None,
                extra={"stub": True, "user_id": user_id},
            )
        ],
    )


@tool
def query_operational(question: str, user_id: str) -> dict:
    """Run a hybrid similarity + relational query against the Lakebase operational
    store: 'similar quality issues for this supplier, scoped to the SKUs the user can
    access, joined to on-hand inventory and open POs.'

    Args:
        question: Natural-language question.
        user_id: The acting user (drives the in-query access-scope predicate).

    Returns:
        OperationalResult shape — question, sql (always returned for traceability), rows.

    NOTE: this is a stub. WS2 owns the real implementation. The shape is the contract.
    """
    return query_operational_impl(question, user_id).model_dump()
