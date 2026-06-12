"""Post-deploy smoke verification for the one-shot Lakebase-perms deploy.

Run AFTER `make deploy` (bundle deploy + app deploy + seed) to confirm the deployed state matches
the permission contract — without poking the UI by hand:

  1. Lakebase is reachable (the configured branch/endpoint).
  2. The operational synced tables in `public` are SELECTable (the SELECT-only read path).
  3. The SP-owned write-back schema (`lakebase_writeback_schema`) exists and its three Meridian
     write-back tables (approved_actions / planning_parameters / constraints) are present.
  4. (Best-effort) the deployed App's service principal HAS the operational SELECT grant — checked
     via Postgres `has_schema_privilege` / `has_table_privilege` for the SP role, when the SP can
     be resolved from the app.

Idempotent and read-only (no GRANTs, no writes). Exit code 0 = all checks passed; non-zero = a
check failed (so it's CI/Make-friendly). Mirrors data/operational/05_grant_app_sp.py's bootstrap
(REPO_ROOT on sys.path; config from agent_server.config; connect via the data-gen Lakebase helper).

Run locally:
    uv run python scripts/verify_deploy.py
    APP_NAME=supply-chain-planner uv run python scripts/verify_deploy.py   # also check the SP grant
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[1])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Local only: load .env before importing settings (mirrors the data-gen scripts; CLAUDE.md).
if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

from agent_server.config import settings  # noqa: E402
from data.operational._lakebase import connect  # noqa: E402

_WRITEBACK_TABLES = ("approved_actions", "planning_parameters", "constraints")
_OPERATIONAL_TABLES = ("quality_incidents", "inventory_current", "open_pos", "user_access")


def _ok(label: str) -> None:
    print(f"  PASS  {label}")


def _fail(label: str, detail: str, failures: list[str]) -> None:
    print(f"  FAIL  {label}: {detail}")
    failures.append(label)


def _resolve_app_sp(app_name: str) -> str | None:
    """The App SP client id (its Postgres role name), or None if not resolvable."""
    try:
        from databricks.sdk import WorkspaceClient

        app = WorkspaceClient().apps.get(name=app_name)
    except Exception as exc:  # noqa: BLE001
        print(f"  ..    could not resolve app {app_name!r}: {exc}")
        return None
    return getattr(app, "service_principal_client_id", None)


def _check_operational_selectable(cur, schema: str, failures: list[str]) -> None:
    for table in _OPERATIONAL_TABLES:
        label = f"SELECT {schema}.{table}"
        try:
            cur.execute(f"SELECT 1 FROM {schema}.{table} LIMIT 1")
            cur.fetchall()
            _ok(label)
        except Exception as exc:  # noqa: BLE001
            cur.connection.rollback()
            _fail(label, str(exc).splitlines()[0], failures)


def _check_writeback_tables(cur, schema: str, failures: list[str]) -> None:
    # Schema present?
    cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,))
    if cur.fetchone():
        _ok(f"write-back schema {schema!r} exists")
    else:
        _fail(f"write-back schema {schema!r}", "not found", failures)
        return
    for table in _WRITEBACK_TABLES:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        )
        if cur.fetchone():
            _ok(f"write-back table {schema}.{table} exists")
        else:
            _fail(f"write-back table {schema}.{table}", "not found", failures)


def _check_sp_grant(cur, sp: str, schema: str, failures: list[str]) -> None:
    """Best-effort: does the App SP role have USAGE on the operational schema + SELECT on its
    tables? Uses Postgres privilege predicates so we read the SP's effective grants, not our own."""
    try:
        cur.execute("SELECT has_schema_privilege(%s, %s, 'USAGE')", (sp, schema))
        if cur.fetchone()[0]:
            _ok(f"SP {sp} has USAGE on {schema!r}")
        else:
            _fail(f"SP USAGE on {schema!r}", "missing", failures)
        cur.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')", (sp, f"{schema}.quality_incidents")
        )
        if cur.fetchone()[0]:
            _ok(f"SP {sp} has SELECT on {schema}.quality_incidents")
        else:
            _fail(f"SP SELECT on {schema}.quality_incidents", "missing", failures)
    except Exception as exc:  # noqa: BLE001 — SP role may not exist yet / privilege fn unsupported
        cur.connection.rollback()
        print(f"  ..    SP grant check skipped: {str(exc).splitlines()[0]}")


def main() -> int:
    op_schema = settings.lakebase_operational_schema
    wb_schema = settings.lakebase_writeback_schema
    app_name = os.environ.get("APP_NAME")

    print("Verifying deploy against the Lakebase permission contract")
    print(f"  operational schema : {op_schema!r}")
    print(f"  write-back schema  : {wb_schema!r}")
    if op_schema == wb_schema:
        print("  FAIL  operational and write-back schemas must differ")
        return 2

    failures: list[str] = []
    try:
        with connect() as conn, conn.cursor() as cur:
            _ok("connected to Lakebase")
            _check_operational_selectable(cur, op_schema, failures)
            _check_writeback_tables(cur, wb_schema, failures)
            if app_name:
                sp = _resolve_app_sp(app_name)
                if sp:
                    _check_sp_grant(cur, sp, op_schema, failures)
                else:
                    print(f"  ..    SP grant check skipped (app {app_name!r} not resolvable)")
            else:
                print("  ..    SP grant check skipped (set APP_NAME to check the deployed SP grant)")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  could not connect to Lakebase: {exc}")
        return 2

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("OK: all deploy checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
