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

# (source_table, primary_key_columns, scheduling_policy)
SYNC_SPECS = [
    ("inventory_current", ["sku"], "CONTINUOUS"),
    ("open_pos", ["supplier_id", "sku"], "CONTINUOUS"),
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
    print(f"Source Delta      : {src_prefix}.*\n")

    for table, pk, policy in SYNC_SPECS:
        target = f"{lakebase_catalog}.{pg_schema}.{table}"
        # Idempotent: skip if the synced table already exists.
        existing = _run(["postgres", "get-synced-table", f"synced_tables/{target}"])
        if existing.returncode == 0:
            print(f"  ✓ exists, skipping: {target}\n")
            continue

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
