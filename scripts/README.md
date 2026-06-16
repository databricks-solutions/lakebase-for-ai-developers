# scripts/ — setup, seed, deploy, and tooling

Operational scripts for deploying and validating the app. The one-shot deploy
(`make deploy`) is a thin wrapper over `deploy.sh`; see [`../docs/DEPLOY.md`](../docs/DEPLOY.md).

## Contents
- **`deploy.sh`** — all deploy logic (idempotent, cold-start-safe, graceful per-step
  degradation): preflight · Lakebase project · build · `bundle deploy` + `bundle run` · seed ·
  Genie · verify. Invoked by the `Makefile` targets (`deploy`, `redeploy`, `redeploy-ui`).
- **`ensure_lakebase_project.py`** — ensures the autoscaling Lakebase project/branch exists before deploy.
- **`grant_lakebase_permissions.py`** — grants a user/SP the Postgres DB access the app needs
  (the auto-grant covers CONNECT+CREATE; SELECT is granted explicitly).
- **`verify_deploy.py`** — post-deploy smoke checks against the running app.
- **`integration_test.sh`** + **`itest_teardown.py`** — cold-start end-to-end deploy test in an
  isolated worktree with always-safe teardown (see [`../docs/test/integration-testing.md`](../docs/test/integration-testing.md)).
- **`install-skills.sh`** — pulls the **non-vendored** extra skills at pinned versions; the core
  skills are already vendored in `.claude/skills/` (see `.claude/skills/UPSTREAM.md`).

## Environment & entrypoints
Create the env with `uv sync` against [`../pyproject.toml`](../pyproject.toml). The app
entrypoints are declared there under `[project.scripts]`: `start-server` (and its `start-app`
alias) run the agent server; `agent-evaluate` runs the evaluation flywheel.
