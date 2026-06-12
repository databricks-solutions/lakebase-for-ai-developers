"""Integration test: the Lakebase permission contract end-to-end (LIVE Lakebase).

Skipped by default — see tests/conftest.py. Run with:
    RUN_INTEGRATION=1 DATABRICKS_CONFIG_PROFILE=<p> uv run pytest tests/integration/test_lakebase_perms.py

Exercises the deployed-SP-shaped path against a real branch:
  1. ensure_memory_schema() + ensure_writeback_tables() succeed and are IDEMPOTENT (run twice).
  2. a SELECT against a `public` synced table works (the operational read path, SELECT-only SP).
  3. a write-back ROUND-TRIP: write_committed_actions(...) for a synthetic thread, then read the
     row back from settings.lakebase_writeback_schema (the same query the webapp /state/tables uses)
     and assert it landed in the WRITE-BACK schema — not `public`.

NOTE: locally this connects as the developer identity (a Lakebase superuser/owner), so it proves
the SQL + schema wiring, not the least-privilege SP grant itself (that's enforced on the deployed
App). The grant slimming + `postgres`-resource contract is covered offline in test_grant_app_sp.py
and test_bundle_config.py; the deployed-SP grant is verified post-deploy by scripts/verify_deploy.py.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def _settings():
    from agent_server.config import settings

    return settings


def test_ensure_schemas_idempotent():
    from agent_server.operational_db import ensure_memory_schema, ensure_writeback_tables

    # Twice each — must not raise (CREATE ... IF NOT EXISTS).
    ensure_memory_schema()
    ensure_memory_schema()
    ensure_writeback_tables()
    ensure_writeback_tables()


def test_public_synced_table_is_selectable():
    """The operational read path: a SELECT against a `public` synced table returns (0+ rows)."""
    from agent_server.operational_db import operational_pool

    settings = _settings()
    schema = settings.lakebase_operational_schema
    with operational_pool().connection() as conn, conn.cursor() as cur:
        # quality_incidents is the pre-seeded pgvector table the hybrid query reads.
        cur.execute(f"SELECT count(*) FROM {schema}.quality_incidents")
        (count,) = cur.fetchone()
        assert count is not None and count >= 0


def test_writeback_roundtrip_lands_in_writeback_schema():
    """write_committed_actions → read back from the write-back schema. Asserts the row is in
    `settings.lakebase_writeback_schema`, reusing build_writeback_rows + the webapp read query."""
    from agent_server.contracts import ActionFact, ActionKind, PlannedAction
    from agent_server.operational_db import (
        build_writeback_rows,
        ensure_writeback_tables,
        operational_pool,
        write_committed_actions,
    )

    settings = _settings()
    wb_schema = settings.lakebase_writeback_schema
    thread_id = f"itest-{uuid.uuid4().hex[:12]}"
    user_id = "integration-test@databricks.com"

    ensure_writeback_tables()

    # A single, fully-specified expedite action → lands in approved_actions.
    action = PlannedAction(
        key="expedite-po-itest-0001",
        kind=ActionKind.EXPEDITE_PO,
        title="Expedite open PO ITEST-0001",
        detail="Pull in the PO to cover the synthetic test gap.",
        target_table="approved_actions",
        qty=123.0,
        cost_delta=4567.0,
        sku="SKU-ITEST",
        supplier_id="SUP-ITEST",
        po_id="ITEST-0001",
        facts=[ActionFact(k="on-hand", v="40 units")],
        default_status="approve",
    )

    # Sanity: the pure shaper routes it to approved_actions with our identifiers.
    staged = build_writeback_rows(thread_id, user_id, "synthetic integration commit", {}, [action])
    assert len(staged["approved_actions"]) == 1
    assert staged["approved_actions"][0]["action_key"] == "expedite-po-itest-0001"

    try:
        ledger = write_committed_actions(
            thread_id, user_id, "synthetic integration commit", {}, [action]
        )
        assert ledger["counts"]["approved_actions"] == 1

        # Read it back from the WRITE-BACK schema (the same query shape webapp /state/tables uses).
        with operational_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT action_key, kind, sku, qty, status, user_id "
                f"FROM {wb_schema}.approved_actions WHERE thread_id = %s",
                (thread_id,),
            )
            rows = cur.fetchall()
        assert len(rows) == 1, f"expected exactly one row in {wb_schema}.approved_actions"
        action_key, kind, sku, qty, status, got_user = rows[0]
        assert action_key == "expedite-po-itest-0001"
        assert kind == ActionKind.EXPEDITE_PO.value
        assert sku == "SKU-ITEST"
        assert float(qty) == 123.0
        assert status == "approve"
        assert got_user == user_id

        # And it must NOT have leaked into the operational schema (`public`).
        op_schema = settings.lakebase_operational_schema
        assert op_schema != wb_schema
        with operational_pool().connection() as conn, conn.cursor() as cur:
            # If a `public.approved_actions` somehow exists, it must not have OUR thread's row.
            try:
                cur.execute(
                    f"SELECT count(*) FROM {op_schema}.approved_actions WHERE thread_id = %s",
                    (thread_id,),
                )
                (leaked,) = cur.fetchone()
                assert leaked == 0, "write-back row must not appear in the operational schema"
            except Exception:  # noqa: BLE001 — no such table in `public` is the expected good case
                conn.rollback()
    finally:
        # Clean up the synthetic row so reruns stay idempotent.
        try:
            with operational_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {wb_schema}.approved_actions WHERE thread_id = %s", (thread_id,)
                )
                conn.commit()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
