# scripts/ — setup, seed, and tooling

> **Scaffold placeholder.** Bring over the template's `scripts/` (`quickstart.py`,
> `start_app.py`, `discover_tools.py`, `preflight.py`, `grant_lakebase_permissions.py`) when
> WS1 stands up the spine:
> https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced/scripts

## Present now
- `install-skills.sh` — pulls the **non-vendored** extras (other Databricks Agent Skills +
  MLflow Skills) at pinned versions. The relevant skills are already vendored in
  `.claude/skills/` (see `.claude/skills/UPSTREAM.md`).

## Environment
The Phase-0 dependency contract is [`../pyproject.toml`](../pyproject.toml) (deps only — no
entrypoints yet). Create the env with `uv sync`. When WS1 brings the template's `agent_server/`
+ this `scripts/` dir, add the `[project.scripts]` entrypoints (`start-app`, `start-server`,
`quickstart`, …) and reconcile with the template's `pyproject.toml`.

## To add (WS1 / WS4)
- `quickstart` — auth + profile + Lakebase config + MLflow experiment (`uv run quickstart`).
- Setup/seed — create UC objects + seed the Acme dataset and pgvector tables/indexes; wired as
  the DABs `setup_and_seed` job so `bundle deploy + seed` is the demo recovery path.
