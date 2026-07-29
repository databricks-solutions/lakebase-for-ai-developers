"""Offline tests for the DABs bundle (databricks.yml) deploy contract.

Pure YAML parsing — no Databricks, no `databricks bundle validate`. Asserts the three bundle
changes the one-shot Lakebase-perms deploy needs:
  1. a `postgres` app resource (registers the SP role + grants CAN_CONNECT_AND_CREATE on the DB),
  2. the app passes LAKEBASE_WRITEBACK_SCHEMA in its config.env (so the SP write-back targets the
     SP-owned schema, not `public`),
  3. the seed job's `grant_app_sp` task depends on `sync_to_lakebase` (so the synced tables exist
     before the SELECT grants run).
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BUNDLE_PATH = _REPO_ROOT / "databricks.yml"
_APP_KEY = "supply_chain_planner"
_SEED_JOB_KEY = "setup_and_seed"


def _bundle() -> dict:
    assert _BUNDLE_PATH.exists(), f"missing {_BUNDLE_PATH}"
    return yaml.safe_load(_BUNDLE_PATH.read_text(encoding="utf-8"))


def _app(bundle: dict) -> dict:
    apps = bundle.get("resources", {}).get("apps", {})
    assert _APP_KEY in apps, f"app key {_APP_KEY!r} not found under resources.apps"
    return apps[_APP_KEY]


def _resource_entries(app: dict) -> list[dict]:
    """The app's `resources:` list (experiment, postgres, …). Each entry is a dict whose single
    non-`name` key names the resource type (e.g. {'name': 'pg', 'postgres': {...}})."""
    return app.get("resources", []) or []


# ── Sanity: the bundle parses and the app is present ──────────────────────────────────────────

def test_bundle_parses_and_app_present():
    app = _app(_bundle())
    assert app.get("name")  # the deployed App name
    assert "config" in app


# ── (1) `postgres` app resource ───────────────────────────────────────────────────────────────

def test_app_declares_postgres_resource():
    app = _app(_bundle())
    pg_entries = [e for e in _resource_entries(app) if isinstance(e, dict) and "postgres" in e]
    assert pg_entries, "app must declare a `postgres` resource (key `postgres`) in its resources list"


def test_postgres_resource_has_branch_database_and_connect_create_permission():
    app = _app(_bundle())
    pg_entries = [e for e in _resource_entries(app) if isinstance(e, dict) and "postgres" in e]
    assert pg_entries, "no `postgres` resource found"
    pg = pg_entries[0]["postgres"]
    assert "branch" in pg, "postgres resource must carry `branch`"
    assert "database" in pg, "postgres resource must carry `database`"
    assert "permission" in pg, "postgres resource must carry `permission`"
    assert pg["permission"] == "CAN_CONNECT_AND_CREATE", (
        f"permission must be CAN_CONNECT_AND_CREATE (so the SP can create its own schemas), "
        f"got {pg['permission']!r}"
    )


# ── (2) LAKEBASE_WRITEBACK_SCHEMA in the app env ──────────────────────────────────────────────

def _env_names(app: dict) -> set[str]:
    env = app.get("config", {}).get("env", []) or []
    return {e.get("name") for e in env if isinstance(e, dict)}


def test_app_env_includes_writeback_schema():
    app = _app(_bundle())
    assert "LAKEBASE_WRITEBACK_SCHEMA" in _env_names(app), (
        "the app must export LAKEBASE_WRITEBACK_SCHEMA so write-back targets the SP-owned schema"
    )


def test_app_env_still_pins_operational_schema_to_public():
    # The move splits write-back off `public` but the synced READ path still reads `public` by
    # default. The env now wires to the `lakebase_operational_schema` var (per-target overridable
    # on shared catalogs) rather than a hard-coded literal, so assert the wiring + that its default
    # is still `public`.
    bundle = _bundle()
    app = _app(bundle)
    env = {e["name"]: e.get("value") for e in app["config"]["env"] if isinstance(e, dict)}
    assert env.get("LAKEBASE_OPERATIONAL_SCHEMA") == "${var.lakebase_operational_schema}"
    var = bundle.get("variables", {}).get("lakebase_operational_schema", {})
    assert var.get("default") == "public", (
        "the operational read path must still default to `public` (synced tables land there)"
    )


# ── (3) grant_app_sp seed task depends on sync_to_lakebase ─────────────────────────────────────

def _seed_tasks(bundle: dict) -> list[dict]:
    jobs = bundle.get("resources", {}).get("jobs", {})
    assert _SEED_JOB_KEY in jobs, f"seed job {_SEED_JOB_KEY!r} not found"
    return jobs[_SEED_JOB_KEY].get("tasks", []) or []


def test_seed_job_has_grant_app_sp_task():
    tasks = _seed_tasks(_bundle())
    keys = [t.get("task_key") for t in tasks]
    assert "grant_app_sp" in keys, f"seed job must include a grant_app_sp task; got {keys}"


def test_grant_app_sp_depends_on_sync_to_lakebase():
    tasks = _seed_tasks(_bundle())
    grant = next((t for t in tasks if t.get("task_key") == "grant_app_sp"), None)
    assert grant is not None, "grant_app_sp task missing"
    deps = grant.get("depends_on", []) or []
    dep_keys = {d.get("task_key") for d in deps if isinstance(d, dict)}
    assert "sync_to_lakebase" in dep_keys, (
        f"grant_app_sp must depend on sync_to_lakebase (synced tables must exist before the SELECT "
        f"grants); got depends_on={sorted(dep_keys)}"
    )
