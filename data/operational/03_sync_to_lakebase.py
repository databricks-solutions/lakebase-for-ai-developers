"""03 — Create/refresh Lakebase Synced Tables (Delta → Postgres) for the relational tables.

Every operational table EXCEPT `quality_incidents` (the pgvector one, handled by 02) reaches
Lakebase via managed Synced Tables — a read-only Postgres mirror of the Delta gold tables:

  Continuous (CDF, near-real-time): inventory_current, open_pos      — change with operations
  Snapshot   (full copy):           suppliers, product_dim, supplier_status, user_access

DABs cannot manage Autoscaling synced tables (see the databricks-lakebase skill), so this drives
the `databricks postgres` CLI. Idempotent: existing synced tables are left in place (a re-run
checks `get-synced-table` first). After sync, the Databricks App's service principal needs GRANTs
on these tables — printed at the end.

Run locally (`uv run python data/operational/03_sync_to_lakebase.py`) or as a job. Requires the
Lakebase identifiers in `.env`: LAKEBASE_UC_CATALOG, LAKEBASE_AUTOSCALING_PROJECT,
LAKEBASE_AUTOSCALING_BRANCH (and the source Delta tables from step 01).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent_server.config import settings

# The two LIVE operational tables follow the configurable LAKEBASE_SYNC_MODE — SNAPSHOT (default,
# a one-time copy that goes idle: cheap, fine for the static seeded demo) or CONTINUOUS (an
# always-on DLT pipeline streaming CDF, for a live-update demo). The dimension tables are always
# SNAPSHOT. Re-running with a different LAKEBASE_SYNC_MODE flips the live tables (delete+recreate).
_LIVE_MODE = settings.lakebase_sync_mode

# (source_table, primary_key_columns, scheduling_policy)
SYNC_SPECS = [
    ("inventory_current", ["sku"], _LIVE_MODE),
    ("open_pos", ["supplier_id", "sku"], _LIVE_MODE),
    ("suppliers", ["supplier_id"], "SNAPSHOT"),
    ("product_dim", ["sku"], "SNAPSHOT"),
    ("supplier_status", ["supplier_id", "last_updated"], "SNAPSHOT"),
    ("user_access", ["user_id", "scope"], "SNAPSHOT"),
]


def _require(value: str | None, name: str) -> str:
    if not value:
        sys.exit(f"Missing required config: {name}. Set it in .env (see data/operational/README.md).")
    return value


def _profile_flag() -> list[str]:
    if not settings.on_databricks and settings.databricks_profile:
        return ["--profile", settings.databricks_profile]
    return []


def _run(args: list[str]) -> subprocess.CompletedProcess:
    print("  $ databricks " + " ".join(args))
    return subprocess.run(["databricks", *args, *_profile_flag()], capture_output=True, text=True)


def _current_mode(get_stdout: str) -> str | None:
    """Infer an existing synced table's policy from its detailed_state (the CLI doesn't echo the
    scheduling_policy). CONTINUOUS streams (…CONTINUOUS_UPDATE); everything else is a SNAPSHOT-class
    one-time/triggered copy."""
    try:
        state = json.loads(get_stdout).get("status", {}).get("detailed_state", "")
    except (ValueError, AttributeError):
        return None
    return "CONTINUOUS" if "CONTINUOUS" in state else "SNAPSHOT"


def _drop_pg_table(table: str, schema: str) -> None:
    """Deleting a synced table leaves its read-only Postgres table behind; drop it so the recreate
    in the new mode starts clean (DROP TABLE is the one mutation allowed on a synced pg table)."""
    from data.operational._lakebase import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {schema}.{table}")
        conn.commit()


def main() -> None:
    lakebase_catalog = _require(settings.lakebase_uc_catalog, "LAKEBASE_UC_CATALOG")
    project = _require(settings.lakebase_autoscaling_project, "LAKEBASE_AUTOSCALING_PROJECT")
    branch_id = _require(settings.lakebase_autoscaling_branch, "LAKEBASE_AUTOSCALING_BRANCH")
    branch = f"projects/{project}/branches/{branch_id}"
    pg_schema = settings.lakebase_operational_schema
    src_prefix = f"{settings.uc_catalog}.{settings.uc_schema}"

    print(f"Lakebase catalog : {lakebase_catalog}")
    print(f"Branch           : {branch}")
    print(f"Postgres schema  : {pg_schema}")
    print(f"Source Delta      : {src_prefix}.*")
    print(f"Live sync mode   : {_LIVE_MODE}  (LAKEBASE_SYNC_MODE — set CONTINUOUS for a live demo)\n")

    for table, pk, policy in SYNC_SPECS:
        target = f"{lakebase_catalog}.{pg_schema}.{table}"
        full = f"synced_tables/{target}"
        # Idempotent + mode-reconciling: skip if it already exists in the desired mode. If the mode
        # differs (flipping inventory_current/open_pos SNAPSHOT↔CONTINUOUS) there is no
        # update-synced-table for scheduling_policy, so delete + drop the orphaned pg table +
        # recreate below.
        existing = _run(["postgres", "get-synced-table", full])
        if existing.returncode == 0:
            current = _current_mode(existing.stdout)
            if current == policy:
                print(f"  ✓ exists ({policy}), skipping: {target}\n")
                continue
            print(f"  ↻ {current or 'unknown'} → {policy}: deleting + recreating {target}")
            deleted = _run(["postgres", "delete-synced-table", full])
            if deleted.returncode != 0:
                print(f"    ! delete failed: {deleted.stderr.strip()}\n")
                continue
            _drop_pg_table(table, pg_schema)

        spec = {
            "spec": {
                "source_table_full_name": f"{src_prefix}.{table}",
                "primary_key_columns": pk,
                "scheduling_policy": policy,
                "branch": branch,
                "postgres_database": settings.lakebase_database,
                "create_database_objects_if_missing": True,
                "new_pipeline_spec": {
                    "storage_catalog": settings.uc_catalog,  # a regular UC catalog for DLT metadata
                    "storage_schema": "default",
                },
            }
        }
        print(f"  creating {policy} synced table: {target}")
        result = _run(["postgres", "create-synced-table", target, "--json", json.dumps(spec)])
        if result.returncode != 0:
            print(f"    ! failed: {result.stderr.strip()}")
        else:
            print(f"    started (use get-synced-table to watch status)\n")

    print("\nNext steps:")
    print("  • Watch status: databricks postgres get-synced-table synced_tables/<catalog>.<schema>.<table>")
    print("  • Grant the App service principal read access on the synced + pgvector tables, e.g.:")
    print(f"      GRANT USAGE ON SCHEMA {pg_schema} TO \"<app-sp-client-id>\";")
    print(f"      GRANT SELECT ON ALL TABLES IN SCHEMA {pg_schema} TO \"<app-sp-client-id>\";")
    print("  • Then run 04_verify_hybrid_query.py")


if __name__ == "__main__":
    main()
