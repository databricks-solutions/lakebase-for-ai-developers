"""Runtime data access for the Operational agent — owned by the app (ships in the app wheel).

The `data/` package is dev/setup tooling and is NOT packaged into the App, so the operational
tool can't import `data.operational._lakebase` at runtime. This module provides the same two
primitives the hybrid query needs — a Lakebase connection pool and a query-embedding call —
built on the supported `databricks_ai_bridge.lakebase` pool (the same OAuth-managed machinery as
the checkpointer/store), pointed at the operational schema rather than the memory schema.

`embed_query` + `vector_literal` mirror `data/operational/_lakebase.py` so the read path can't
drift from the write/seed path.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Optional, Sequence

from databricks.sdk import WorkspaceClient
from databricks_ai_bridge.lakebase import LakebasePool

from agent_server.config import settings
from agent_server.contracts import ActionDecision, ActionKind, PlannedAction
from agent_server.lakebase import init_lakebase_config

logger = logging.getLogger(__name__)

# Per-kind column values for the structural tables (decided by CODE so the row shape can't drift
# from the kind taxonomy). planning_parameters.parameter and constraints.kind both fall back to
# their table default when an unexpected kind lands there.
_PARAMETER_BY_KIND: dict[ActionKind, str] = {
    ActionKind.RAISE_SAFETY_STOCK: "safety_stock",
    ActionKind.TIGHTEN_INSPECTION: "inspection_level",
}
_CONSTRAINT_KIND_BY_KIND: dict[ActionKind, str] = {
    ActionKind.ALLOCATION_CONSTRAINT: "allocation",
    ActionKind.SUPPLIER_QUALITY_HOLD: "supplier_hold",
}


@lru_cache(maxsize=1)
def _ws() -> WorkspaceClient:
    return WorkspaceClient()


@lru_cache(maxsize=1)
def operational_pool() -> LakebasePool:
    """Sync Lakebase pool scoped to the operational schema (the pre-seeded pgvector
    `quality_incidents` + the synced relational tables). Sync so it runs safely inside the
    graph's sync gather node (LangGraph executes sync nodes in a worker thread)."""
    cfg = init_lakebase_config()
    return LakebasePool(
        instance_name=cfg.instance_name,
        autoscaling_endpoint=cfg.autoscaling_endpoint,
        project=cfg.autoscaling_project,
        branch=cfg.autoscaling_branch,
        schema=settings.lakebase_operational_schema,
    )


def vector_literal(vec: list[float]) -> str:
    """pgvector text form '[1,2,3]' for casting `::vector` (matches data/operational/_lakebase.py)."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def embed_query(text: str) -> list[float]:
    """Embed one query string via the Databricks embedding serving endpoint."""
    resp = _ws().serving_endpoints.query(name=settings.embedding_endpoint, input=[text])
    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
    if not data:
        raise RuntimeError(f"Unexpected embedding response from {settings.embedding_endpoint}: {resp!r}")
    item = data[0]
    emb = getattr(item, "embedding", None)
    if emb is None and isinstance(item, dict):
        emb = item.get("embedding")
    if emb is None:
        raise RuntimeError(f"No embedding in response from {settings.embedding_endpoint}: {resp!r}")
    return list(emb)


# ── Meridian write-back tables (the structured commit target) ─────────────────────────────
# Native Lakebase Postgres tables (NOT synced tables — the agent writes them directly at
# commit). They live in the operational schema alongside `quality_incidents` so the demo's
# Lakebase tab shows the human's decision as real relational rows. DDL mirrors the style of
# data/operational/02_pre_seed_pgvector.py; CREATE IF NOT EXISTS so it's idempotent and the
# seed scripts that fully own `quality_incidents` are untouched.

_SCHEMA = settings.lakebase_operational_schema

_DDL_APPROVED_ACTIONS = f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.approved_actions (
      action_key   text NOT NULL,
      thread_id    text NOT NULL,
      kind         text,
      po_id        text,
      supplier_id  text,
      sku          text,
      qty          numeric,
      cost_delta   numeric,
      status       text,
      rationale    text,
      user_id      text,
      created_at   timestamptz DEFAULT now(),
      PRIMARY KEY (thread_id, action_key)
    )
"""

_DDL_PLANNING_PARAMETERS = f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.planning_parameters (
      thread_id    text NOT NULL,
      sku          text NOT NULL,
      parameter    text NOT NULL DEFAULT 'safety_stock',
      old_value    numeric,
      new_value    numeric,
      rationale    text,
      user_id      text,
      created_at   timestamptz DEFAULT now(),
      PRIMARY KEY (thread_id, sku, parameter)
    )
"""

_DDL_CONSTRAINTS = f"""
    CREATE TABLE IF NOT EXISTS {_SCHEMA}.constraints (
      thread_id      text NOT NULL,
      constraint_key text NOT NULL,
      kind           text DEFAULT 'allocation',
      sku            text,
      program        text,
      detail         text,
      rationale      text,
      user_id        text,
      created_at     timestamptz DEFAULT now(),
      PRIMARY KEY (thread_id, constraint_key)
    )
"""

_WRITEBACK_DDL = (_DDL_APPROVED_ACTIONS, _DDL_PLANNING_PARAMETERS, _DDL_CONSTRAINTS)


def ensure_writeback_tables() -> None:
    """Idempotently create the three Meridian write-back tables. Sync (uses operational_pool)."""
    with operational_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")
        for ddl in _WRITEBACK_DDL:
            cur.execute(ddl)
        conn.commit()


def _resolved_qty(action: PlannedAction, decision: Optional[ActionDecision]) -> float | None:
    """An explicit edited_qty from the human overrides the planner's proposed qty."""
    if decision is not None and decision.edited_qty is not None:
        return decision.edited_qty
    return action.qty


def _resolved_status(action: PlannedAction, decision: Optional[ActionDecision]) -> str:
    """Per-action status: the human's choice if given, else the planner's default."""
    if decision is not None:
        return decision.status
    return action.default_status


def build_writeback_rows(
    thread_id: str,
    user_id: Optional[str],
    rationale: Optional[str],
    decisions_by_key: Mapping[str, ActionDecision],
    planned_actions: Sequence[PlannedAction],
) -> dict[str, list[dict[str, Any]]]:
    """Map (planned actions + per-action decisions) → the per-table row dicts to INSERT.

    Pure: no DB/LLM, fully unit-testable. Held actions (status='hold') are recorded with that
    status in `approved_actions` (so the audit trail shows what was *declined*) but are NOT
    written to `planning_parameters` / `constraints` — only approved structural changes land there.
    `edited_qty` overrides the proposed qty; `safety_stock_override` becomes the new_value.
    """
    rows: dict[str, list[dict[str, Any]]] = {
        "approved_actions": [],
        "planning_parameters": [],
        "constraints": [],
    }
    for action in planned_actions:
        decision = decisions_by_key.get(action.key)
        status = _resolved_status(action, decision)
        qty = _resolved_qty(action, decision)

        if action.target_table == "approved_actions":
            rows["approved_actions"].append({
                "action_key": action.key,
                "thread_id": thread_id,
                "kind": action.kind.value,
                "po_id": action.po_id,
                "supplier_id": action.supplier_id,
                "sku": action.sku,
                "qty": qty,
                "cost_delta": action.cost_delta,
                "status": status,
                "rationale": rationale,
                "user_id": user_id,
            })
            continue

        # planning_parameters / constraints carry only APPROVED structural changes; a held action
        # is still audited as a (hold) row in approved_actions so the decision is never silent.
        if status != "approve":
            rows["approved_actions"].append({
                "action_key": action.key,
                "thread_id": thread_id,
                "kind": action.kind.value,
                "po_id": action.po_id,
                "supplier_id": action.supplier_id,
                "sku": action.sku,
                "qty": qty,
                "cost_delta": action.cost_delta,
                "status": status,
                "rationale": rationale,
                "user_id": user_id,
            })
            continue

        if action.target_table == "planning_parameters":
            new_value = qty
            if decision is not None and decision.safety_stock_override is not None:
                new_value = decision.safety_stock_override
            rows["planning_parameters"].append({
                "thread_id": thread_id,
                "sku": action.sku,
                # raise_safety_stock → 'safety_stock'; tighten_inspection → 'inspection_level'.
                "parameter": _PARAMETER_BY_KIND.get(action.kind, "safety_stock"),
                "old_value": action.qty_min,
                "new_value": new_value,
                "rationale": rationale,
                "user_id": user_id,
            })
        elif action.target_table == "constraints":
            rows["constraints"].append({
                "thread_id": thread_id,
                "constraint_key": action.key,
                # allocation_constraint → 'allocation'; supplier_quality_hold → 'supplier_hold'.
                "kind": _CONSTRAINT_KIND_BY_KIND.get(action.kind, "allocation"),
                "sku": action.sku,
                "program": action.program,
                "detail": action.detail,
                "rationale": rationale,
                "user_id": user_id,
            })
    return rows


# INSERT ... ON CONFLICT (pk) DO UPDATE — idempotent on resume/retry. One per table; the column
# lists match build_writeback_rows' dict keys exactly.
_UPSERT_APPROVED_ACTIONS = f"""
    INSERT INTO {_SCHEMA}.approved_actions
      (action_key, thread_id, kind, po_id, supplier_id, sku, qty, cost_delta, status, rationale, user_id)
    VALUES
      (%(action_key)s, %(thread_id)s, %(kind)s, %(po_id)s, %(supplier_id)s, %(sku)s, %(qty)s,
       %(cost_delta)s, %(status)s, %(rationale)s, %(user_id)s)
    ON CONFLICT (thread_id, action_key) DO UPDATE SET
      kind = EXCLUDED.kind, po_id = EXCLUDED.po_id, supplier_id = EXCLUDED.supplier_id,
      sku = EXCLUDED.sku, qty = EXCLUDED.qty, cost_delta = EXCLUDED.cost_delta,
      status = EXCLUDED.status, rationale = EXCLUDED.rationale, user_id = EXCLUDED.user_id
"""

_UPSERT_PLANNING_PARAMETERS = f"""
    INSERT INTO {_SCHEMA}.planning_parameters
      (thread_id, sku, parameter, old_value, new_value, rationale, user_id)
    VALUES
      (%(thread_id)s, %(sku)s, %(parameter)s, %(old_value)s, %(new_value)s, %(rationale)s, %(user_id)s)
    ON CONFLICT (thread_id, sku, parameter) DO UPDATE SET
      old_value = EXCLUDED.old_value, new_value = EXCLUDED.new_value,
      rationale = EXCLUDED.rationale, user_id = EXCLUDED.user_id
"""

_UPSERT_CONSTRAINTS = f"""
    INSERT INTO {_SCHEMA}.constraints
      (thread_id, constraint_key, kind, sku, program, detail, rationale, user_id)
    VALUES
      (%(thread_id)s, %(constraint_key)s, %(kind)s, %(sku)s, %(program)s, %(detail)s,
       %(rationale)s, %(user_id)s)
    ON CONFLICT (thread_id, constraint_key) DO UPDATE SET
      kind = EXCLUDED.kind, sku = EXCLUDED.sku, program = EXCLUDED.program,
      detail = EXCLUDED.detail, rationale = EXCLUDED.rationale, user_id = EXCLUDED.user_id
"""

_UPSERTS = {
    "approved_actions": _UPSERT_APPROVED_ACTIONS,
    "planning_parameters": _UPSERT_PLANNING_PARAMETERS,
    "constraints": _UPSERT_CONSTRAINTS,
}


def write_committed_actions(
    thread_id: str,
    user_id: Optional[str],
    rationale: Optional[str],
    decisions_by_key: Mapping[str, ActionDecision],
    planned_actions: Sequence[PlannedAction],
) -> dict[str, Any]:
    """Persist the human's committed decision as real relational rows in ONE transaction.

    Thin execution wrapper: the row-shaping lives in `build_writeback_rows`. Idempotent — the
    per-row INSERT ... ON CONFLICT DO UPDATE means a resume/retry re-commit overwrites cleanly.
    Returns a ledger dict (per-table counts + the staged rows) for the done payload + UI tab.
    """
    rows = build_writeback_rows(thread_id, user_id, rationale, decisions_by_key, planned_actions)
    with operational_pool().connection() as conn, conn.cursor() as cur:
        for table, params in rows.items():
            for row in params:
                cur.execute(_UPSERTS[table], row)
        conn.commit()
    return {
        "counts": {table: len(params) for table, params in rows.items()},
        "rows": rows,
    }


__all__ = [
    "operational_pool",
    "embed_query",
    "vector_literal",
    "ensure_writeback_tables",
    "build_writeback_rows",
    "write_committed_actions",
]
