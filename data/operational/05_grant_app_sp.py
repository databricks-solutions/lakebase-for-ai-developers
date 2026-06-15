"""05 — Grant the deployed App's service principal the data privileges its DABs resource bindings
can't cover: operational synced tables (Lakebase) + MLflow trace tables (Unity Catalog).

The Databricks App runs its non-OBO work — the agent-memory store, the LangGraph checkpointer, and
the operational hybrid query — as a dedicated **service principal** that the Apps platform mints at
deploy time (its client id is the app's DATABRICKS_CLIENT_ID). Lakebase maps each Databricks
identity to a Postgres role named after that client id.

What now handles WHAT:
- The native **`postgres` app resource** (databricks.yml, `permission: CAN_CONNECT_AND_CREATE`)
  makes the Apps platform **auto-register that Postgres role** and grant it **CONNECT + CREATE on
  the database** — so the SP can create + own its OWN schemas (agent memory + write-back) at
  startup with no manual setup. (That's why this task no longer registers a role or grants
  CREATE-on-database — the prior versions did, before the resource existed.)

This task does the two things those resource bindings canNOT — **object-level data grants on
securables the SP doesn't own**:
- **Lakebase: USAGE + SELECT on the operational (`public`) synced tables.** Owned by the platform's
  `databricks_writer_*` role, not the SP, so CONNECT+CREATE never reaches them.
- **Unity Catalog: USE CATALOG + USE SCHEMA/CREATE TABLE/MODIFY/SELECT on the MLflow trace schema**
  (`<trace_catalog>.<mlflow_trace_schema>`). The `experiment` app resource grants CAN_MANAGE on the
  experiment *object* (a workspace ACL), but MLflow 3 UC tracing writes spans as rows in a UC Delta
  table — a separate governance plane CAN_MANAGE doesn't reach. Without this the app's UC trace bind
  fails PERMISSION_DENIED and silently falls back to artifact-storage tracing, which is egress-blocked
  on Apps → traces land with **no span data**.

Both need an explicit GRANT run by an admin/superuser (the deployer). Hence this stays a seed task.

OBO data reads happen as the *end user* (the user-auth OAuth client), so this grant is only ever
the App SP — never the user-auth client id.

Why this is a seed task: `make deploy` deploys the app (creating its SP) BEFORE running the seed
job, so by the time this runs the SP exists and is resolvable. It runs as the deployer — the same
identity the rest of the seed runs as, which must be a Lakebase superuser on the branch (the
project creator is). Idempotent and best-effort-but-loud: if the app isn't deployed yet (a
SEED-only run before the first app deploy) it logs and skips rather than failing the seed.

Run locally: `uv run python data/operational/05_grant_app_sp.py` (needs the same .env Lakebase
coords as 03/04, plus APP_NAME — defaults to the bundle's app name. The UC trace grant runs through
get_spark(), so it also needs the serverless/Databricks-Connect compute the other data scripts use).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from psycopg import sql

from agent_server.config import settings
from data.operational._lakebase import connect

# Single source of truth for the app name is the `app_name` bundle var, passed into the seed env as
# APP_NAME (see databricks.yml &seed_cfg). Default matches the bundle default for local runs.
APP_NAME = os.environ.get("APP_NAME", "supply-chain-planner")


def _ws():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _resolve_app_sp(w, app_name: str) -> str | None:
    """The App's service-principal client id (the Postgres role name). None if the app isn't
    deployed yet or the SDK can't read it — caller treats that as skip, not failure."""
    try:
        app = w.apps.get(name=app_name)
    except Exception as exc:  # app not deployed yet, or SDK/permission issue → skip, don't fail
        print(f"  ! could not resolve app {app_name!r}: {exc}")
        return None
    sp = getattr(app, "service_principal_client_id", None)
    if not sp:
        print(f"  ! app {app_name!r} has no service_principal_client_id yet")
    return sp


def _grant_uc_trace_writes(sp: str) -> None:
    """Grant the App SP the Unity Catalog privileges MLflow 3 UC tracing needs to create + write the
    trace Delta tables. Runs on the serverless Spark session as the deployer (a UC admin on the
    catalog), the same idiom as 00_bootstrap_schemas.py — so no SQL warehouse id is needed here.
    Catalog/schema come from config (mirrors the bootstrap defaults); the SP is the resolved app
    client id. Idempotent (re-GRANT is a no-op) and best-effort: a UC hiccup logs loudly but does
    not fail the seed, so the operational grants below still run."""
    catalog = settings.mlflow_trace_catalog or settings.uc_catalog
    schema = settings.mlflow_trace_schema or "mlflow_traces"
    if not catalog:
        print("  ! skipping UC trace grant: no MLFLOW_TRACE_CATALOG / UC_CATALOG configured")
        return

    from data._spark import get_spark

    spark = get_spark()
    # Service principals are referenced by their application (client) id, backtick-quoted, in UC
    # GRANTs — same identity the Lakebase Postgres role is named after.
    stmts = [
        f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{sp}`",
        f"GRANT USE SCHEMA, CREATE TABLE, MODIFY, SELECT ON SCHEMA `{catalog}`.`{schema}` TO `{sp}`",
    ]
    print(f"UC trace grant: {catalog}.{schema} → SP {sp}")
    for s in stmts:
        try:
            spark.sql(s)
            print(f"  ✓ {s}")
        except Exception as exc:  # don't let a UC permission hiccup block the operational grant
            print(f"  ! UC trace grant failed (traces may fall back to artifact storage): {exc}")


def main() -> None:
    app_name = APP_NAME

    w = _ws()
    sp = _resolve_app_sp(w, app_name)
    if not sp:
        # Best-effort: a SEED-only run before the first app deploy. The app's own startup will
        # surface the missing grants; re-run this task (make deploy runs it automatically) once the
        # app exists.
        print(f"Skipping app-SP grants: app {app_name!r} not deployed yet.")
        return

    print(f"App           : {app_name}")
    print(f"App SP        : {sp}\n")

    # 1) Unity Catalog: trace-table writes for MLflow 3 UC tracing. Independent of Lakebase — runs
    #    whenever a trace catalog is configured.
    _grant_uc_trace_writes(sp)

    # 2) Lakebase: operational synced-table reads. Needs the autoscaling coords.
    project = settings.lakebase_autoscaling_project
    branch_id = settings.lakebase_autoscaling_branch
    if not (project and branch_id):
        print("\nSkipping operational SP grant: LAKEBASE_AUTOSCALING_PROJECT/BRANCH not set.")
        return
    branch = f"projects/{project}/branches/{branch_id}"
    schema = settings.lakebase_operational_schema

    print(f"\nBranch        : {branch}")
    print(f"Granting on   : schema {schema!r} (operational synced tables)\n")

    role = sql.Identifier(sp)
    sch = sql.Identifier(schema)
    stmts = [
        # operational reads for the app SP (synced + pgvector tables in the schema). The `postgres`
        # app resource already gave the SP CONNECT + CREATE on the database; SELECT on these
        # platform-owned synced tables is the only piece it can't cover.
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sch, role),
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(sch, role),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(sch, role),
    ]
    with connect() as conn, conn.cursor() as cur:
        for s in stmts:
            cur.execute(s)
            print(f"  ✓ {s.as_string(conn)}")
        conn.commit()
    print("\nApp SP granted. Restart/redeploy the app if it was already running so it picks these up.")


if __name__ == "__main__":
    main()
