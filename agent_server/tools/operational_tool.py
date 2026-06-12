"""Operational agent tool — the Lakebase hybrid query.

Runs the canonical operational query in ONE governed SQL statement: vector similarity over the
pre-seeded `quality_incidents` (pgvector) + JOINs to live on-hand inventory and open POs. This is
the project thesis — similarity as one predicate inside a relational/operational query, not a
vector-index round-trip + app-side join.

The SQL mirrors `data/operational/04_verify_hybrid_query.py` (the WS2 spike that validates the
hero scenario) and is always returned on `OperationalResult.sql` for traceability/scoring.

Access control: operational reads run as the app service principal, so every authenticated app
user sees the same UC-governed data. Fine-grained per-user product-code scoping is intentionally
out of scope for the demo; in production it would be added via Postgres RLS keyed on
`current_user()` (with per-user/OBO DB connections) or an entitlements join driven by a real
identity source.
"""

from __future__ import annotations

from collections.abc import Mapping

from langchain_core.tools import tool

from agent_server.config import settings
from agent_server.contracts import OperationalResult, OperationalRow
from agent_server.operational_db import embed_query, operational_pool, vector_literal

_SCHEMA = settings.lakebase_operational_schema

# Hybrid similarity + relational query. Schema-qualified so it resolves regardless of the
# connection search_path. Keep in sync with data/operational/04_verify_hybrid_query.py.
HYBRID_SQL = f"""
SELECT m.incident_id, m.summary, m.supplier_id, m.sku, m.category,
       i.on_hand_qty, po.open_po_qty,
       round((1 - (m.embedding <=> %(q)s::vector))::numeric, 3) AS similarity
FROM {_SCHEMA}.quality_incidents m
JOIN {_SCHEMA}.inventory_current i ON m.sku = i.sku
JOIN {_SCHEMA}.open_pos          po ON m.supplier_id = po.supplier_id AND m.sku = po.sku
WHERE m.expired_at IS NULL
ORDER BY m.embedding <=> %(q)s::vector
LIMIT 5
"""


def _as_float(v) -> float | None:
    return None if v is None else float(v)


def query_operational_impl(question: str) -> OperationalResult:
    """Embed the question, run the hybrid SQL, normalize to OperationalResult."""
    qvec = vector_literal(embed_query(question))

    rows: list[OperationalRow] = []
    with operational_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(HYBRID_SQL, {"q": qvec})
        cols = [c.name for c in cur.description]
        for record in cur.fetchall():
            # LakebasePool cursors yield mapping rows; plain cursors yield tuples — handle both.
            r = record if isinstance(record, Mapping) else dict(zip(cols, record))
            rows.append(
                OperationalRow(
                    sku=r.get("sku"),
                    supplier_id=r.get("supplier_id"),
                    summary=r.get("summary"),
                    similarity=_as_float(r.get("similarity")),
                    on_hand_qty=_as_float(r.get("on_hand_qty")),
                    open_po_qty=_as_float(r.get("open_po_qty")),
                    extra={"incident_id": r.get("incident_id"), "category": r.get("category")},
                )
            )

    return OperationalResult(question=question, sql=HYBRID_SQL, rows=rows)


@tool
def query_operational(question: str) -> dict:
    """Run a hybrid similarity + relational query against the Lakebase operational store:
    'similar quality issues for this supplier, joined to on-hand inventory and open POs.'

    Args:
        question: Natural-language question.

    Returns:
        OperationalResult shape — question, sql (always returned for traceability), rows.
    """
    return query_operational_impl(question).model_dump()
