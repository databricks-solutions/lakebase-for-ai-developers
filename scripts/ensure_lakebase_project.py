"""Idempotent get-or-create of the Lakebase autoscaling project, then wait until it's AVAILABLE.

`make deploy` runs this first so a fresh workspace is genuinely one-shot — no manual
`databricks postgres create-project` prereq. The `postgres` app resource and the operational synced
tables both target this project (by its id), so it must exist and be ready BEFORE `bundle deploy`.

The same code runs locally (from the Makefile, auth via DATABRICKS_CONFIG_PROFILE) and would run
on Databricks (ambient creds) — only the auth source changes, per the repo's env-awareness pattern
(see data/operational/_lakebase.py / agent_server/config.py).

Project id resolution (first that applies wins):
  1. argv[1]                                  — explicit, e.g. `python scripts/ensure_lakebase_project.py my-proj`
  2. LAKEBASE_PROJECT / lakebase_project env  — what `make lakebase-project` forwards
  3. settings.lakebase_autoscaling_project    — the typed config (LAKEBASE_AUTOSCALING_PROJECT)
  4. DEFAULT_PROJECT_ID                        — the bundle's `lakebase_project` default

Defensive: if the project already exists we log and return 0 (the common re-deploy case). Creation
uses the SDK `w.postgres.create_project` long-running operation (`.wait()`), then we poll
`get_project` until its provisioning state reads AVAILABLE/ACTIVE/READY.
"""

from __future__ import annotations

import os
import sys
import time

# Env-awareness: only load a local .env when NOT on Databricks (DBR/Apps set this var). Mirrors the
# rest of the repo so `WorkspaceClient()` picks up DATABRICKS_CONFIG_PROFILE locally / ambient creds
# on Databricks.
if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass  # dotenv is dev-only; absence just means rely on the ambient/profile credential chain

from agent_server.config import settings

# Matches the bundle's `lakebase_project` default (databricks.yml) — keep in sync.
DEFAULT_PROJECT_ID = "mfg-supply-chain-copilot"
PG_VERSION = 17  # DEPLOYMENT_GUIDE §3: create-project ... '{"spec": {"pg_version": "17", ...}}'
POLL_TIMEOUT_S = 600
POLL_INTERVAL_S = 10
# Provisioning/branch states that mean "ready to use". The SDK exposes states across a few enums
# (e.g. ProvisioningInfoState.ACTIVE, BranchStatusState.READY) and the raw API uses AVAILABLE — accept
# any of them so we're robust to the SDK version and to where the state surfaces.
_READY_STATES = {"AVAILABLE", "ACTIVE", "READY"}


def _resolve_project_id() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    for env_key in ("LAKEBASE_PROJECT", "lakebase_project"):
        val = os.environ.get(env_key)
        if val and val.strip():
            return val.strip()
    if settings.lakebase_autoscaling_project:
        return settings.lakebase_autoscaling_project
    return DEFAULT_PROJECT_ID


def _ws():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _project_state(project) -> str:
    """Best-effort extraction of a project's provisioning/readiness state across SDK versions —
    the field isn't uniform, so probe a few likely locations and normalize to an upper-case string.
    Returns '' if nothing readable is found (caller treats unknown as not-yet-ready)."""
    status = getattr(project, "status", None)
    candidates = []
    if status is not None:
        for attr in ("state", "provisioning_state", "detailed_state"):
            candidates.append(getattr(status, attr, None))
        prov = getattr(status, "provisioning_info", None)
        if prov is not None:
            candidates.append(getattr(prov, "state", None))
    candidates.append(getattr(project, "state", None))
    for c in candidates:
        if c is None:
            continue
        # Enums → use their .value/.name; plain strings pass through.
        text = getattr(c, "value", None) or getattr(c, "name", None) or str(c)
        return str(text).upper()
    return ""


def _wait_available(w, project_id: str) -> bool:
    deadline = time.time() + POLL_TIMEOUT_S
    while time.time() < deadline:
        proj = w.postgres.get_project(name=f"projects/{project_id}")
        state = _project_state(proj)
        if any(r in state for r in _READY_STATES):
            print(f"  project state: {state or 'AVAILABLE'} — ready")
            return True
        if "FAILED" in state:
            print(f"  ! project state: {state} — provisioning failed")
            return False
        print(f"  project state: {state or 'provisioning'} — waiting {POLL_INTERVAL_S}s ...")
        time.sleep(POLL_INTERVAL_S)
    print(f"  ! project not ready within {POLL_TIMEOUT_S}s (continuing — deploy may still work)")
    return False


def main() -> int:
    project_id = _resolve_project_id()
    name = f"projects/{project_id}"
    print(f"Ensuring Lakebase autoscaling project: {project_id}")

    w = _ws()

    # 1. Already there? (the common re-deploy case) — log and return.
    try:
        existing = w.postgres.get_project(name=name)
        state = _project_state(existing)
        print(f"  ✓ project {project_id!r} already exists (state: {state or 'unknown'}).")
        if state and not any(r in state for r in _READY_STATES) and "FAILED" not in state:
            print("  still provisioning — waiting for it to become available ...")
            _wait_available(w, project_id)
        return 0
    except Exception as exc:
        # Not-found is the create path; anything else (auth/permission) is surfaced loudly below.
        msg = str(exc)
        if not ("not found" in msg.lower() or "does not exist" in msg.lower()
                or "RESOURCE_DOES_NOT_EXIST" in msg or "NOT_FOUND" in msg):
            print(f"  ! could not read project {project_id!r}: {msg}")
            print("    (if this is an auth/permission error, check the profile + Lakebase entitlement)")
            # Fall through and attempt create — a transient read error shouldn't block a fresh deploy.

    # 2. Create it. Use the typed SDK long-running op; fall back to the raw REST shape from
    #    DEPLOYMENT_GUIDE §3 if the typed dataclasses differ across SDK versions.
    print(f"  creating project {project_id!r} (pg_version={PG_VERSION}) ...")
    display_name = project_id.replace("-", " ").replace("_", " ").title()
    try:
        from databricks.sdk.service.postgres import Project, ProjectSpec

        op = w.postgres.create_project(
            project=Project(spec=ProjectSpec(display_name=display_name, pg_version=PG_VERSION)),
            project_id=project_id,
        )
        # create_project returns a long-running operation; wait() blocks until provisioning is done.
        wait = getattr(op, "wait", None)
        if callable(wait):
            wait()
        print(f"  ✓ created project {project_id!r}")
    except Exception as exc:
        msg = str(exc)
        if "already exists" in msg.lower() or "ALREADY_EXISTS" in msg:
            print(f"  ✓ project {project_id!r} already exists (raced create).")
            _wait_available(w, project_id)
            return 0
        # Raw-REST fallback — matches §3: POST /api/2.0/postgres/projects?project_id=<id>.
        print(f"  typed create failed ({msg}); retrying via raw REST ...")
        try:
            w.api_client.do(
                "POST", "/api/2.0/postgres/projects",
                query={"project_id": project_id},
                body={"spec": {"display_name": display_name, "pg_version": str(PG_VERSION)}},
            )
            print(f"  ✓ created project {project_id!r} (raw REST)")
        except Exception as exc2:
            msg2 = str(exc2)
            if "already exists" in msg2.lower() or "ALREADY_EXISTS" in msg2:
                print(f"  ✓ project {project_id!r} already exists.")
                _wait_available(w, project_id)
                return 0
            print(f"  ! failed to create project {project_id!r}: {msg2}")
            return 1

    # 3. Poll until AVAILABLE so downstream `bundle deploy` (postgres resource + synced tables) works.
    _wait_available(w, project_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
