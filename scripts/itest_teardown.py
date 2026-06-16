"""Teardown for the cold-start integration test (scripts/integration_test.sh).

Deletes the throwaway resources a test run created OUTSIDE the bundle. `bundle destroy` handles the
in-bundle resources (app + SP, experiment, job, dev/demo warehouse, Genie space); this script removes
the two it can't:

  1. The throwaway **UC schema** (+ its operational Delta tables) in the (existing) test catalog.
  2. The throwaway **Lakebase autoscaling project** — deleting the whole project also nukes the app
     SP's orphaned memory/write-back schemas. That orphan is unavoidable once the app SP is deleted
     (it OWNS those schemas; databricks_superuser can't reassign them — see
     docs/lakebase-apps-permissions.md). Using a per-run throwaway project is exactly what makes
     `bundle destroy` safe here: the only schemas the SP ever owns live in a project we then drop.

SAFETY — refuses to touch any name that doesn't carry the throwaway marker ("itest") unless --force,
so a mistyped project/schema can't nuke real data. Best-effort: each step is independent; a failure is
warned, not fatal, so one stuck step never blocks the rest of teardown (exit 0 unless --strict).

Env-aware (loads .env locally, ambient on Databricks) like the rest of scripts/ + data/. Run from the
main repo (not the worktree — same code, and the worktree is removed last):

    uv run python scripts/itest_teardown.py --project scp-itest-1700000000 \
        --catalog main --schema scp_itest_1700000000
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[1])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Local only: load .env so DATABRICKS_CONFIG_PROFILE / coords resolve (mirrors the data-gen scripts).
if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 — dotenv is dev-only; fall back to the ambient/profile chain
        pass

# The substring a throwaway name MUST contain before we'll delete it (defense against fat-fingering a
# real project/schema). The orchestrator names resources `scp-itest-<ts>` / `scp_itest_<ts>`.
ITEST_MARKER = "itest"


def _warn(msg: str) -> None:
    print(f"  ⚠ {msg}")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _guard(name: str, kind: str, force: bool) -> bool:
    """True if it's safe to delete `name`. A name without the throwaway marker is refused unless
    --force (so teardown can't be pointed at a real project/schema by mistake)."""
    if ITEST_MARKER in name or force:
        return True
    _warn(
        f"refusing to delete {kind} {name!r}: no {ITEST_MARKER!r} marker (use --force to override). "
        "This guard stops the harness from nuking a real (non-throwaway) resource."
    )
    return False


def drop_uc_schema(catalog: str, schema: str, force: bool, failures: list[str]) -> None:
    """DROP SCHEMA … CASCADE via get_spark() (Databricks Connect locally — no warehouse needed, so it
    works regardless of whether bundle destroy already removed the dev/demo warehouse)."""
    if not _guard(schema, "schema", force):
        return
    try:
        from data._spark import get_spark

        spark = get_spark()
        spark.sql(f"DROP SCHEMA IF EXISTS `{catalog}`.`{schema}` CASCADE")
        _ok(f"dropped UC schema {catalog}.{schema} (CASCADE)")
    except Exception as exc:  # noqa: BLE001 — leaked schema is recoverable; never block teardown
        _warn(
            f"could not drop UC schema {catalog}.{schema}: {str(exc).splitlines()[0]} "
            "(local drop needs serverless_compute_id=auto in the profile — drop it by hand if it leaked)"
        )
        failures.append(f"schema:{catalog}.{schema}")


def delete_lakebase_project(project_id: str, force: bool, failures: list[str]) -> None:
    """Delete the throwaway Lakebase autoscaling project (typed SDK, raw-REST fallback). Idempotent:
    a not-found project is treated as already gone."""
    if not _guard(project_id, "Lakebase project", force):
        return
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    name = f"projects/{project_id}"
    try:
        w.postgres.delete_project(name=name)
        _ok(f"deleted Lakebase project {project_id!r}")
        return
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if any(s in msg.lower() for s in ("not found", "does not exist")) or "NOT_FOUND" in msg:
            _ok(f"Lakebase project {project_id!r} already gone")
            return
        # Typed call may differ across SDK versions — retry the raw REST shape before giving up.
        try:
            w.api_client.do("DELETE", f"/api/2.0/postgres/projects/{project_id}")
            _ok(f"deleted Lakebase project {project_id!r} (raw REST)")
            return
        except Exception as exc2:  # noqa: BLE001
            msg2 = str(exc2)
            if any(s in msg2.lower() for s in ("not found", "does not exist")) or "NOT_FOUND" in msg2:
                _ok(f"Lakebase project {project_id!r} already gone")
                return
            _warn(f"could not delete Lakebase project {project_id!r}: {msg2.splitlines()[0]}")
            failures.append(f"project:{project_id}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Tear down throwaway integration-test resources.")
    ap.add_argument("--project", help="Throwaway Lakebase project id to delete.")
    ap.add_argument("--catalog", help="UC catalog holding the throwaway schema (not deleted).")
    ap.add_argument("--schema", help="Throwaway UC schema to DROP … CASCADE.")
    ap.add_argument("--force", action="store_true", help="Delete even without the 'itest' marker.")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero if any step failed.")
    args = ap.parse_args()

    print("Integration-test teardown (throwaway schema + Lakebase project)")
    failures: list[str] = []

    if args.catalog and args.schema:
        drop_uc_schema(args.catalog, args.schema, args.force, failures)
    if args.project:
        delete_lakebase_project(args.project, args.force, failures)

    if failures:
        _warn(f"{len(failures)} teardown step(s) need manual cleanup: {', '.join(failures)}")
        return 1 if args.strict else 0
    print("  teardown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
