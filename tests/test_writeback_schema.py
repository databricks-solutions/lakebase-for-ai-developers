"""Offline tests for the write-back schema move (SP-owned schema, not `public`).

The Meridian HITL commit writes its structured rows (approved_actions / planning_parameters /
constraints) to a schema the app service principal OWNS — `settings.lakebase_writeback_schema`
(default `supply_chain_planner_app`) — NOT the operational/synced schema (`public`), which stays
SELECT-only for the least-privilege SP. The synced-table READ path (operational_pool +
operational_tool._SCHEMA) must still target `lakebase_operational_schema`.

No network: every assertion inspects module-level string constants. Mirrors test_meridian.py's
plain-pytest-function style — plain imports of agent_server modules, no fixtures/classes.
"""

from __future__ import annotations

from agent_server import operational_db
from agent_server.config import settings
from agent_server.tools import operational_tool


# ── The three schemas are distinct by config (the whole point of the move) ────────────────────

def test_writeback_schema_distinct_from_operational():
    assert settings.lakebase_writeback_schema
    assert settings.lakebase_operational_schema
    assert settings.lakebase_writeback_schema != settings.lakebase_operational_schema


def test_writeback_schema_default_is_sp_owned_app_schema():
    # Default value documented in CLAUDE.md / config.py: the SP-owned app schema.
    assert settings.lakebase_writeback_schema == "supply_chain_planner_app"
    assert settings.lakebase_operational_schema == "public"


# ── Write-back DDL + upsert SQL are qualified with the WRITE-BACK schema, not operational ──────

_DDL_CONSTANTS = (
    "_DDL_APPROVED_ACTIONS",
    "_DDL_PLANNING_PARAMETERS",
    "_DDL_CONSTRAINTS",
)
_UPSERT_CONSTANTS = (
    "_UPSERT_APPROVED_ACTIONS",
    "_UPSERT_PLANNING_PARAMETERS",
    "_UPSERT_CONSTRAINTS",
)


def test_module_writeback_schema_constant_matches_settings():
    # The module pins a single `_WRITEBACK_SCHEMA` constant off settings — the one source of truth
    # for every qualified DDL/upsert string below.
    assert operational_db._WRITEBACK_SCHEMA == settings.lakebase_writeback_schema


def test_writeback_ddl_constants_qualified_with_writeback_schema():
    wb = settings.lakebase_writeback_schema
    for name in _DDL_CONSTANTS:
        ddl = getattr(operational_db, name)
        assert f"{wb}." in ddl, f"{name} must qualify its table with the write-back schema {wb!r}"


def test_writeback_upsert_constants_qualified_with_writeback_schema():
    wb = settings.lakebase_writeback_schema
    for name in _UPSERT_CONSTANTS:
        sql = getattr(operational_db, name)
        assert f"{wb}." in sql, f"{name} must qualify its table with the write-back schema {wb!r}"


def test_writeback_ddl_does_not_reference_operational_schema():
    # The DDL must NOT write into `public` — that schema is SELECT-only for the deployed SP, so a
    # `public.`-qualified CREATE/INSERT would crash on commit ("permission denied for schema public").
    op = settings.lakebase_operational_schema
    for name in _DDL_CONSTANTS + _UPSERT_CONSTANTS:
        text = getattr(operational_db, name)
        assert f"{op}." not in text, (
            f"{name} must NOT reference the operational schema {op!r} — write-back lives in the "
            f"SP-owned schema {settings.lakebase_writeback_schema!r}"
        )


def test_ensure_writeback_creates_the_writeback_schema_not_public():
    # The startup DDL runner exists and is named for the write-back tables (kept loosely coupled —
    # we only assert the public entrypoints survive the schema move).
    assert callable(operational_db.ensure_writeback_tables)
    assert callable(operational_db.ensure_memory_schema)
    # The three write-back DDLs are bundled for the idempotent startup create.
    assert len(operational_db._WRITEBACK_DDL) == 3


# ── The synced READ path stays on the operational schema (`public`) ───────────────────────────

def test_operational_tool_schema_is_operational_not_writeback():
    # The hybrid read query (quality_incidents + inventory_current + open_pos) reads
    # the synced tables in `public` — it must NOT follow the write-back move.
    assert operational_tool._SCHEMA == settings.lakebase_operational_schema
    assert operational_tool._SCHEMA != settings.lakebase_writeback_schema


def test_hybrid_sql_reads_from_operational_schema():
    # The canonical hybrid SQL is qualified with the operational (synced) schema, not write-back.
    op = settings.lakebase_operational_schema
    assert f"{op}.quality_incidents" in operational_tool.HYBRID_SQL
    assert f"{op}.inventory_current" in operational_tool.HYBRID_SQL
    # And it must not accidentally read from the write-back schema.
    assert settings.lakebase_writeback_schema not in operational_tool.HYBRID_SQL
