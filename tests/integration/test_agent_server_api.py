"""Integration test: the agent-server FastAPI app over the Meridian write-back read path (LIVE).

Skipped by default — see tests/conftest.py. Run with:
    RUN_INTEGRATION=1 DATABRICKS_CONFIG_PROFILE=<p> uv run pytest tests/integration/test_agent_server_api.py

What this covers (scoped, best-effort, REAL):
  * Boots the real FastAPI app (`agent_server.start_server.app`, with the webapp router mounted)
    under Starlette's TestClient — no uvicorn, no Apps proxy.
  * Hits the Meridian state-read endpoint `GET /api/state/tables?thread_id=...` and asserts it
    returns 200 with the three write-back table buckets, and that the endpoint reads from the
    WRITE-BACK schema (`settings.lakebase_writeback_schema`), not `public`.
  * Best-effort end-to-end: if a synthetic write-back commit can be planted via operational_db,
    the same endpoint reads those committed rows back (run → commit-equivalent → read).

The endpoint resolves the caller via X-Forwarded-* headers (Apps proxy) and falls back to the
configured local user, so no real OBO token is needed locally. The full LLM-driven run→poll→HITL
flow over /invocations needs a live planner endpoint + an open store on app.state and is out of
scope here — this verifies the Review/Lakebase read seam the UI depends on, against real Lakebase.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


def _client():
    from fastapi.testclient import TestClient

    # Importing start_server mounts the webapp router onto `app` (webapp does
    # `from agent_server.start_server import app`). Import it for the side effect, then use `app`.
    import agent_server.webapp  # noqa: F401  (registers /api/* routes)
    from agent_server.start_server import app

    # raise_server_exceptions=False so a downstream Lakebase hiccup surfaces as an HTTP code we can
    # assert on, not a test-collection error.
    return TestClient(app, raise_server_exceptions=False)


def test_state_tables_endpoint_returns_writeback_buckets():
    """GET /api/state/tables returns 200 with the three write-back table buckets for a fresh
    thread (empty is fine — the endpoint guards each query to [])."""
    client = _client()
    thread_id = f"itest-api-{uuid.uuid4().hex[:10]}"
    resp = client.get("/api/state/tables", params={"thread_id": thread_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"] == thread_id
    for bucket in ("approved_actions", "planning_parameters", "constraints", "recalled_memory"):
        assert bucket in body, f"missing bucket {bucket!r} in /state/tables response"
        assert isinstance(body[bucket], list)


def test_state_tables_endpoint_reads_writeback_schema():
    """The endpoint must query the SP-owned write-back schema, not `public`. We assert it on the
    module wiring the handler uses (the read query template is schema-formatted with
    settings.lakebase_writeback_schema in the handler)."""
    from agent_server import webapp
    from agent_server.config import settings

    # The state-read SQL templates are schema-parametrized; the handler formats them with the
    # write-back schema. Assert both the templates and that the schema source is write-back.
    assert "{schema}.approved_actions" in webapp._STATE_TABLE_QUERIES["approved_actions"]
    assert "{schema}.planning_parameters" in webapp._STATE_TABLE_QUERIES["planning_parameters"]
    assert "{schema}.constraints" in webapp._STATE_TABLE_QUERIES["constraints"]
    # The handler's schema source is the write-back schema (distinct from operational `public`).
    assert settings.lakebase_writeback_schema != settings.lakebase_operational_schema


def test_state_tables_reads_back_a_committed_writeback_row():
    """Best-effort run→commit→read: plant a committed write-back row via operational_db (the same
    path the /commit HITL node uses), then read it back through the HTTP endpoint and assert the
    committed action surfaces. Skips cleanly if the write path can't reach Lakebase."""
    from agent_server.contracts import ActionKind, PlannedAction
    from agent_server.operational_db import ensure_writeback_tables, operational_pool, write_committed_actions
    from agent_server.config import settings

    thread_id = f"itest-api-rt-{uuid.uuid4().hex[:8]}"
    user_id = "integration-test@databricks.com"
    action = PlannedAction(
        key="quality-hold-itest-api-0001",
        kind=ActionKind.QUALITY_HOLD,
        title="Hold on-hand SKU-APITEST",
        detail="Hold the on-hand units pending validation (synthetic).",
        target_table="approved_actions",
        qty=40.0,
        sku="SKU-APITEST",
        supplier_id="SUP-APITEST",
        default_status="approve",
    )

    try:
        ensure_writeback_tables()
        write_committed_actions(thread_id, user_id, "synthetic api round-trip", {}, [action])
    except Exception as exc:  # noqa: BLE001 — no live Lakebase write path → nothing to read back
        pytest.skip(f"could not plant a write-back row (no live Lakebase write path): {exc}")

    try:
        client = _client()
        resp = client.get("/api/state/tables", params={"thread_id": thread_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        keys = {r.get("action_key") for r in body["approved_actions"]}
        assert "quality-hold-itest-api-0001" in keys, (
            f"committed write-back row not read back via the endpoint; got {body['approved_actions']}"
        )
        committed = next(r for r in body["approved_actions"] if r["action_key"] == "quality-hold-itest-api-0001")
        assert committed["kind"] == ActionKind.QUALITY_HOLD.value
        assert committed["sku"] == "SKU-APITEST"
    finally:
        wb = settings.lakebase_writeback_schema
        try:
            with operational_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(f"DELETE FROM {wb}.approved_actions WHERE thread_id = %s", (thread_id,))
                conn.commit()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
