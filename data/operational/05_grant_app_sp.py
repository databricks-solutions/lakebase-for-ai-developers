"""05 — Grant the deployed App's service principal SELECT on the operational synced tables.

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
- This task does the one thing the resource canNOT: grant the SP **USAGE + SELECT on the
  operational (`public`) synced tables**. Those tables are owned by the platform's
  `databricks_writer_*` role, not the SP, so CONNECT+CREATE never reaches them — they need an
  explicit GRANT run by a branch superuser (the deployer). Hence this stays a seed task.

OBO data reads happen as the *end user* (the user-auth OAuth client), so this grant is only ever
the App SP — never the user-auth client id.

Why this is a seed task: `make deploy` deploys the app (creating its SP) BEFORE running the seed
job, so by the time this runs the SP exists and is resolvable. It runs as the deployer — the same
identity the rest of the seed runs as, which must be a Lakebase superuser on the branch (the
project creator is). Idempotent and best-effort-but-loud: if the app isn't deployed yet (a
SEED-only run before the first app deploy) it logs and skips rather than failing the seed.

Run locally: `uv run python data/operational/05_grant_app_sp.py` (needs the same .env Lakebase
coords as 03/04, plus APP_NAME — defaults to the bundle's app name).
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


def main() -> None:
    app_name = APP_NAME
    project = settings.lakebase_autoscaling_project
    branch_id = settings.lakebase_autoscaling_branch
    if not (project and branch_id):
        print("Skipping app-SP grant: LAKEBASE_AUTOSCALING_PROJECT/BRANCH not set.")
        return
    branch = f"projects/{project}/branches/{branch_id}"
    schema = settings.lakebase_operational_schema

    w = _ws()
    sp = _resolve_app_sp(w, app_name)
    if not sp:
        # Best-effort: a SEED-only run before the first app deploy. The app's own startup will
        # surface the missing grant; re-run this task (make deploy runs it automatically) once the
        # app exists.
        print(f"Skipping app-SP grant: app {app_name!r} not deployed yet.")
        return

    print(f"App           : {app_name}")
    print(f"App SP        : {sp}")
    print(f"Branch        : {branch}")
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
