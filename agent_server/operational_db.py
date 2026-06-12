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
# commit). They live in the SP-owned write-back schema (`lakebase_writeback_schema`), NOT the
# operational schema: the app service principal CREATEs + OWNs everything it writes here, so the
# deployed (least-privilege) SP can create them at startup. `public` stays SELECT-only for the SP
# (synced read tables). DDL mirrors the style of data/operational/02_pre_seed_pgvector.py;
# CREATE IF NOT EXISTS so it's idempotent and the seed scripts that own the synced/operational
# tables are untouched.

_WRITEBACK_SCHEMA = settings.lakebase_writeback_schema

_DDL_APPROVED_ACTIONS = f"""
    CREATE TABLE IF NOT EXISTS {_WRITEBACK_SCHEMA}.approved_actions (
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
    CREATE TABLE IF NOT EXISTS {_WRITEBACK_SCHEMA}.planning_parameters (
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
    CREATE TABLE IF NOT EXISTS {_WRITEBACK_SCHEMA}.constraints (
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
    """Idempotently create the SP-owned write-back schema + the three Meridian write-back tables.
    Sync (uses operational_pool). The schema-qualified DDL overrides the pool's operational
    search_path, so running it through operational_pool() is fine."""
    with operational_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_WRITEBACK_SCHEMA}")
        for ddl in _WRITEBACK_DDL:
            cur.execute(ddl)
        conn.commit()


def ensure_memory_schema() -> None:
    """Create the LangGraph agent-memory schema if absent. The checkpointer + store are configured
    with schema=<lakebase_memory_schema>, but databricks_langchain does NOT create it — so when it's
    absent their unqualified `CREATE TABLE`s fall back to `public`, where the least-privilege app SP
    can't create and startup crashes ("permission denied for schema public"). The app SP owns the
    schema it creates (it has CREATE on the database). Idempotent; sync (uses operational_pool).
    No-op when the memory schema is unset (the store/checkpointer then default to public anyway)."""
    schema = settings.lakebase_memory_schema
    if not schema:
        return
    with operational_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.commit()


def _durable_schema_name() -> str:
    """Schema the durable long-running response store persists to. HARD-CODED in
    `databricks_ai_bridge.long_running` (`AGENT_DB_SCHEMA = "agent_server"`) and NOT configurable via
    env — so unlike the memory schema we can't repoint it to a freed name. Imported (not duplicated)
    so it tracks the library if it ever renames; falls back to the documented constant."""
    try:
        from databricks_ai_bridge.long_running.models import AGENT_DB_SCHEMA

        return AGENT_DB_SCHEMA
    except Exception:  # noqa: BLE001 — library internals; the name is stable/documented
        return "agent_server"


def ensure_durable_schema() -> None:
    """Make the connecting role own the durable long-running response schema BEFORE the library
    lazily creates it — and loudly flag the case where it can't.

    `databricks_ai_bridge.long_running` persists background/durable responses (the `responses` +
    `messages` tables behind background mode and its stale-response scanner) to a HARD-CODED schema
    (`agent_server`), created via `CREATE SCHEMA IF NOT EXISTS` at startup — so whoever runs that
    first OWNS it. If a developer ran the durable server locally against this same (shared) Lakebase
    branch, the schema already exists owned by THEIR user; the app SP's later `CREATE … IF NOT
    EXISTS` no-ops, the SP gets no USAGE → every stale-scan iteration fails with
    "permission denied for schema agent_server" and background mode is broken.

    Running this (as the SP) before the durable `init_db()` makes the SP create+own the schema on a
    fresh branch, so the library's later create is a harmless no-op. If the schema already exists
    owned by a DIFFERENT principal (an already-polluted branch) we can't fix it here — a role can't
    reassign a schema it doesn't own — so we log a loud, actionable remediation instead of letting it
    resurface as a buried recurring traceback. Idempotent; sync (uses operational_pool)."""
    schema = _durable_schema_name()
    with operational_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_catalog.pg_get_userbyid(nspowner) AS owner "
            "FROM pg_catalog.pg_namespace WHERE nspname = %s",
            (schema,),
        )
        row = cur.fetchone()
        owner = row["owner"] if row else None

        if owner is None:
            # Fresh branch — create it so the connecting role (the SP on Databricks) owns it.
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            conn.commit()
            logger.info("Created durable response schema %r (owned by the connecting role)", schema)
            return

        cur.execute("SELECT current_user AS role")
        current_role = cur.fetchone()["role"]
        if owner == current_role:
            logger.debug("Durable response schema %r already owned by the connecting role", schema)
            return

        # Foreign-owned (typically created by a developer's local run against the shared branch).
        # The connecting role can't reassign a schema it doesn't own, and `ALTER … OWNER TO <sp>`
        # ALSO fails for the owner unless it can SET ROLE to the SP (membership it usually lacks) —
        # so surface the two remediations an *owner* CAN run without SET ROLE: GRANT access to the
        # SP (keeps ownership), or DROP + let the SP recreate+own it on restart (the clean fix).
        grants = "\n".join(
            [
                f'GRANT USAGE, CREATE ON SCHEMA {schema} TO "{current_role}";',
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO "{current_role}";',
                f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {schema} TO "{current_role}";',
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{current_role}";',
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO "{current_role}";',
            ]
        )
        logger.error(
            "Durable response schema %r is owned by %r, not the connecting role %r — the durable "
            "long-running store (background mode + its stale-response scanner) will fail with "
            "'permission denied for schema %s'. Fix, as %r (the owner; `ALTER … OWNER TO` the SP "
            "needs SET-ROLE membership the owner usually lacks): either GRANT the SP access —\n%s\n"
            "— or, for clean SP ownership, `DROP SCHEMA %s CASCADE;` then restart the app so the SP "
            "recreates+owns it.",
            schema,
            owner,
            current_role,
            schema,
            owner,
            grants,
            schema,
        )


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
    INSERT INTO {_WRITEBACK_SCHEMA}.approved_actions
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
    INSERT INTO {_WRITEBACK_SCHEMA}.planning_parameters
      (thread_id, sku, parameter, old_value, new_value, rationale, user_id)
    VALUES
      (%(thread_id)s, %(sku)s, %(parameter)s, %(old_value)s, %(new_value)s, %(rationale)s, %(user_id)s)
    ON CONFLICT (thread_id, sku, parameter) DO UPDATE SET
      old_value = EXCLUDED.old_value, new_value = EXCLUDED.new_value,
      rationale = EXCLUDED.rationale, user_id = EXCLUDED.user_id
"""

_UPSERT_CONSTRAINTS = f"""
    INSERT INTO {_WRITEBACK_SCHEMA}.constraints
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
    "ensure_memory_schema",
    "ensure_durable_schema",
    "build_writeback_rows",
    "write_committed_actions",
]
