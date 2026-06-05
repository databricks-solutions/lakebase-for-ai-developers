"""04 — Verify the operational hybrid query against the seeded Lakebase data.

This is WS2's "spike the hybrid query first" gate. It runs the resolved hybrid SQL (vector
similarity + relational joins + the v1 in-query access predicate) and asserts the hero scenario
reproduces deterministically:

  As the in-scope demo user (`adhesives`):
    • top result is the Henkel / SKU-1001 adhesive-cracking cluster
    • on_hand_qty = 40, open_po_qty = 500 (Henkel's open PO)
    • the SUPERSEDED (expired_at) incident is excluded
    • Cluster A dominates the top-5 (cracking ranks above the in-scope distractors)
  As the out-of-scope user (`fasteners`/`abrasives`):
    • the adhesive hero cluster never appears — they only ever see their own scope's incidents
      (proves the access predicate; they may still see fastener/abrasive rows)

The access predicate here is the in-query `JOIN user_access ... user_id = %(user_id)s`. Next phase
replaces it with Postgres RLS on `current_user()` (documented in README.md) — same outcome, engine
enforced. Run after 01→03. Exits non-zero on any failed assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent_server.config import settings
from data.operational import seeds
from data.operational._lakebase import connect, embed, resolve_demo_user, vector_literal

SCHEMA = settings.lakebase_operational_schema

# The resolved hybrid query (mirrors the SQL the operational_tool returns for traceability).
HYBRID_SQL = f"""
SELECT m.incident_id, m.summary, m.supplier_id, m.sku, m.category, i.on_hand_qty, po.open_po_qty,
       round((1 - (m.embedding <=> %(q)s::vector))::numeric, 3) AS similarity
FROM {SCHEMA}.quality_incidents m
JOIN {SCHEMA}.inventory_current i ON m.sku = i.sku
JOIN {SCHEMA}.open_pos          po ON m.supplier_id = po.supplier_id AND m.sku = po.sku
JOIN {SCHEMA}.user_access       ua ON ua.scope = m.category AND ua.user_id = %(user_id)s
WHERE m.expired_at IS NULL
ORDER BY m.embedding <=> %(q)s::vector
LIMIT 5
"""


def _run(cur, qvec: str, user_id: str) -> list[dict]:
    cur.execute(HYBRID_SQL, {"q": qvec, "user_id": user_id})
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> None:
    qvec = vector_literal(embed([seeds.HERO_QUERY_TEXT])[0])
    demo_user = resolve_demo_user()  # same identity 01 wrote into user_access
    failures: list[str] = []

    with connect() as conn, conn.cursor() as cur:
        # ── In-scope demo user ───────────────────────────────────────────────────────────────
        rows = _run(cur, qvec, demo_user)
        print(f"\n=== As {demo_user} (scope: adhesives) — {len(rows)} rows ===")
        for r in rows:
            print(f"  {r['similarity']}  {r['supplier_id']}/{r['sku']} [{r['category']}]  "
                  f"on_hand={r['on_hand_qty']} open_po={r['open_po_qty']}  {r['summary']}")

        def check(cond: bool, msg: str) -> None:
            if not cond:
                failures.append(msg)

        check(len(rows) >= 1, "demo user got no rows")
        if rows:
            top = rows[0]
            check(top["supplier_id"] == seeds.HERO_SUPPLIER_ID and top["sku"] == seeds.HERO_SKU,
                  f"top row not the hero supplier/SKU: {top['supplier_id']}/{top['sku']}")
            check(abs(float(top["on_hand_qty"]) - seeds.HERO_ON_HAND) < 1e-6,
                  f"top on_hand_qty {top['on_hand_qty']} != {seeds.HERO_ON_HAND}")
            check(abs(float(top["open_po_qty"]) - seeds.HERO_HENKEL_OPEN_PO_QTY) < 1e-6,
                  f"top open_po_qty {top['open_po_qty']} != {seeds.HERO_HENKEL_OPEN_PO_QTY}")
            check(not any("SUPERSEDED" in (r["summary"] or "") for r in rows),
                  "expired (SUPERSEDED) incident leaked into results")
            # Cluster A is uniquely the hero supplier+SKU rows (distractors never reuse HERO_SKU),
            # so match on BOTH to prove the cracking cluster dominates — not just any Henkel row.
            hero_rows = sum(1 for r in rows
                            if r["supplier_id"] == seeds.HERO_SUPPLIER_ID and r["sku"] == seeds.HERO_SKU)
            check(hero_rows >= 4, f"cluster A did not dominate top-5 (only {hero_rows}/5 from hero supplier+SKU)")

        # ── Out-of-scope user ────────────────────────────────────────────────────────────────
        # Governance proof: they NEVER see the adhesive hero cluster — only their own scope's
        # incidents (fastener/abrasive rows may appear; that's correct, not a leak).
        oos = _run(cur, qvec, seeds.OUT_OF_SCOPE_USER)
        print(f"\n=== As {seeds.OUT_OF_SCOPE_USER} (scope: fasteners/abrasives) — {len(oos)} rows ===")
        for r in oos:
            print(f"  {r['similarity']}  {r['supplier_id']}/{r['sku']} [{r['category']}]  {r['summary']}")
        check(not any(r["category"] == seeds.HERO_CATEGORY for r in oos),
              "out-of-scope user saw adhesive rows — access predicate failed")
        check(not any(r["supplier_id"] == seeds.HERO_SUPPLIER_ID and r["sku"] == seeds.HERO_SKU for r in oos),
              "out-of-scope user saw the hero incident — access predicate failed")

    print("\n" + ("✗ FAILED:\n  - " + "\n  - ".join(failures) if failures else "✓ All hybrid-query assertions passed."))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
