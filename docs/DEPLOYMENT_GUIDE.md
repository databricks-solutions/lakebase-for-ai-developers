# Deployment Guide — Supply-Chain Planner Copilot on a new workspace

End-to-end walkthrough for standing up the **entire demo** — the agent, the **chat UI**, the demo
data, and MLflow tracing — on a brand-new Databricks workspace, with one command. Everything in
the repo deploys as a single Databricks Asset Bundle (DABs); nothing is deployed by hand.

> Quick version once prereqs are in place: `make deploy PROFILE=<your-cli-profile>`.
> For the one-paragraph TL;DR see [`DEPLOY.md`](DEPLOY.md). This guide is the full story,
> including the **permissions checklist**.

---

## 1. What gets deployed

One `make deploy` produces all of this:

| Asset | Bundle resource | Where it runs / lives |
|---|---|---|
| **LangGraph agent** (supervisor → gather → planner → HITL) | `apps.supply_chain_planner` | In-process in the App (not Model Serving) |
| **Your React chat UI** | same App | Served at **`/ui`** by `agent_server/webapp.py` |
| Backend-explorer + history + 👍/👎 feedback APIs | same App | `/api/*` |
| Demo dataset (operational tables, pgvector, Genie space, Vector Search index) | `jobs.setup_and_seed` | Unity Catalog + Lakebase |
| MLflow experiment (tracing target) | `experiments.planner_experiment` | Fresh per deploy |

There is **no separate frontend deployment** — the App process serves the agent, your `/api/*`
routes, and the compiled UI together. The build step (`npm run build`) produces `frontend/dist`,
which the bundle ships via `sync.include`.

---

## 2. Prerequisites

### Tooling (your laptop)
- [ ] **Databricks CLI** ≥ 0.230 (`databricks -v`)
- [ ] **Node.js 18+** and npm (builds the SPA)
- [ ] **uv** (`pip install uv`) — used by the App runtime and local runs
- [ ] **psql** (only if you run `scripts/grant_app_sp.sh` for the Lakebase grants)

### Workspace facts to collect
You'll plug these into the bundle variables in §4:
- [ ] Workspace URL → for the CLI profile
- [ ] A **Unity Catalog catalog** you can write to (e.g. `main` or a sandbox catalog)
- [ ] A **Lakebase autoscaling project** (create one in §3)
- [ ] *(optional, for tracing)* a **SQL warehouse id**

> **Easiest path:** deploy as a **workspace admin** who is also a **metastore admin**. That covers
> almost every grant in §6 automatically. The checklist below spells out the exact privileges if
> you're not an admin or are handing roles to a service account.

> **⚠️ The workspace must have serverless compute** (the seed job runs on serverless). On
> Field-Engineering vending-machine workspaces that means an **`aws_stable_serverless`** template —
> the `*_classic` templates have **no** serverless and can't run the seed. Serverless is a property
> of the workspace, not a toggle you can flip on.

---

## 3. Create the Lakebase project (one-time)

The agent's durable state (checkpoints + long-term memory) and the operational hybrid query live
in Lakebase autoscaling Postgres. Create a project (UI: **Compute → Lakebase → Create**, or the
`databricks-lakebase-autoscale` skill / CLI):

```bash
# PROJECT_ID is a positional argument (not a --project-id flag).
databricks postgres create-project mfg-supply-chain-copilot \
  --json '{"spec": {"display_name": "MFG Supply-Chain Copilot", "pg_version": "17"}}' \
  --profile <p>
```

This auto-creates a `production` branch, a `primary` endpoint, and the `databricks_postgres`
database — which match the bundle defaults (`lakebase_project` / `lakebase_branch` /
`lakebase_endpoint` / `lakebase_database`). If you use different names, set the matching variables
in §4.

**Register the Lakebase database as a UC catalog** (needed for the operational synced tables). The
autoscaling `create-catalog` CLI has a known body-stripping bug, so do it in the **UI**: *Catalog
Explorer → Create catalog → from a Lakebase database* → name it `mfg_supply_chain_lakebase` (or set
`lakebase_uc_catalog`).

---

## 4. Configure the bundle

Everything workspace-specific is a variable in [`databricks.yml`](../databricks.yml). Set the ones
that differ from the defaults — simplest is to edit the `default:`s once, or pass `--var` flags.

| Variable | Default | Set to |
|---|---|---|
| `uc_catalog` | `main` | your writable catalog |
| `uc_schema` | `supply_chain_planner` | schema for the demo tables |
| `lakebase_project` / `_branch` / `_endpoint` / `_database` | `mfg-supply-chain-copilot` / `production` / `primary` / `databricks_postgres` | your Lakebase coordinates |
| `lakebase_uc_catalog` | `mfg_supply_chain_lakebase` | the catalog from §3 |
| `vector_search_endpoint` | `supply-chain-planner-vs` | VS endpoint name (created by the seed job) |
| `embedding_endpoint` | `databricks-gte-large-en` | a Databricks embedding endpoint |
| `llm_endpoint` | `databricks-claude-opus-4-8` | a Foundation Model endpoint |
| `sql_warehouse_id` | *(blank)* | a warehouse id to **enable** UC tracing (blank = skip tracing) |
| `genie_space_id` | *(blank)* | set **after** the seed job creates it (§5, step 4) |

---

## 5. Deploy

```bash
# 0. authenticate the CLI to the target workspace
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile <p>

# 1. one command: build the SPA, deploy the bundle, seed the demo data.
#    Pass workspace-specific variables via VARS (no need to edit databricks.yml):
make deploy PROFILE=<p> VARS="uc_catalog=<your-writable-catalog>"
```

The seed job is **fully serverless and self-contained** — every data script runs through the
`data/_seed_task.py` launcher (which fixes `sys.path` + injects config, since serverless tasks
get no env vars and no `__file__`), the bundle creates all schemas it needs (including the Lakebase
`public` schema), and `sync_to_lakebase` creates the synced tables via the **REST API** (the
`databricks` CLI is blocked on serverless compute). No manual schema/table creation is required.

That wrapper runs three steps (raw equivalents are in the header of `databricks.yml`):
1. `npm --prefix frontend ci && npm --prefix frontend run build` → `frontend/dist`
2. `databricks bundle deploy -t dev --profile <p>` → App + experiment + job
3. `databricks bundle run setup_and_seed -t dev --profile <p>` → loads demo data

Then the **two post-deploy steps** (these can't be DABs resources):

3. **Grant the App's service principal a Lakebase Postgres role** (the App SP only exists after the
   first deploy, so this is a follow-up):
   ```bash
   PROFILE=<p> ./scripts/grant_app_sp.sh
   ```
   The script resolves the App SP, registers it as a Postgres role, and runs the GRANTs. If your
   Lakebase project/branch differ from the defaults, edit the `BRANCH=` line at the top first, or
   add the role via the Lakebase **UI** (instance → Roles) and re-run.

4. **Wire the Genie space.** The `create_genie_space` seed task prints a Genie space id in its run
   output. Set `genie_space_id` to it and redeploy so the Analytics agent binds:
   ```bash
   make deploy PROFILE=<p> SEED=false       # redeploy app only; data already seeded
   ```
   (Until set, the Analytics/Genie route degrades gracefully — every other route works.)

### Verify
```bash
databricks apps list --profile <p>     # supply-chain-planner → RUNNING; copy the URL
```
Open the App URL `/ui`, sign in, and ask:
*"Have we seen a disruption like the PrecisionBond recall before?"* → you should get a traced
answer, and 👍/👎 should attach as an assessment in the experiment.

---

## 6. Permissions checklist

Three identities are involved. **The bundle automates one grant** (the experiment); the rest are
listed with the exact commands. Minimal-permission by design: **data reads run on-behalf-of-user
(OBO)**, so the App service principal needs *no* SELECT on the demo tables — only the user does.

### A. You — the deployer (runs `make deploy`)
- [ ] **Apps** entitlement — create/deploy Databricks Apps
- [ ] **Serverless** — *Can use* serverless compute (the seed job runs serverless)
- [ ] **Workflows/Jobs** — create jobs (default for workspace users)
- [ ] **MLflow** — create experiments (default for workspace users)
- [ ] UC, on `<uc_catalog>`:
      `USE CATALOG`, `CREATE SCHEMA`, and `CREATE TABLE` + `CREATE VOLUME` on the schemas
      ```sql
      GRANT USE CATALOG, CREATE SCHEMA ON CATALOG <uc_catalog> TO `you@databricks.com`;
      ```
      (or own the catalog / be metastore admin)
- [ ] **Lakebase** — create a project (§3) — workspace admin or the Lakebase entitlement
- [ ] **Vector Search** — create an endpoint + index — VS entitlement + `CREATE` on `<uc_catalog>.<uc_schema>`
- [ ] **Genie** — create a Genie space — Genie entitlement + `CAN MANAGE` on a SQL warehouse
- [ ] **SQL warehouse** — `CAN USE` (Genie, VS sync, the verify task, tracing)

### B. The App service principal (auto-created on first deploy)
Resolve its id: `databricks apps get supply-chain-planner -p <p> -o json` → `service_principal_client_id`.

- [x] **MLflow experiment `CAN_MANAGE`** — **automated by the bundle** (the `experiment`
      app-resource in `databricks.yml`); the SP writes traces here
- [ ] **Lakebase Postgres role** with `CONNECT` + `CREATE` — registered by `scripts/grant_app_sp.sh`
      (the App creates its own memory schema + checkpoint tables). *Manual, post-first-deploy.*
- [ ] **Foundation Model endpoints** `CAN QUERY` on `<llm_endpoint>` **and** `<embedding_endpoint>`
      — the planner/router and the long-term-memory store run as the App SP
      (usually granted to all principals by default; verify in *Serving → endpoint → Permissions*)
- [ ] **MLflow UC tracing** (only if `sql_warehouse_id` is set) — the SP writes trace tables, so:
      ```sql
      GRANT USE CATALOG ON CATALOG <uc_catalog> TO `<app-sp-client-id>`;
      GRANT USE SCHEMA, CREATE TABLE, MODIFY ON SCHEMA <uc_catalog>.<mlflow_trace_schema> TO `<app-sp-client-id>`;
      ```
      plus `CAN USE` on the tracing SQL warehouse. *Leave `sql_warehouse_id` blank to skip all of this.*
- [ ] ❌ **Not needed:** SELECT on the operational/knowledge tables, Genie, or the VS index — those
      run **OBO** as the signed-in user (below).

### C. End users — the planners using the app
- [ ] **App `CAN_USE`**
      ```bash
      databricks apps set-permissions supply-chain-planner --profile <p> \
        --json '{"access_control_list":[{"group_name":"planners","permission_level":"CAN_USE"}]}'
      ```
- [ ] Because data is **OBO**, each user needs their own UC grants on what they query:
      ```sql
      GRANT USE CATALOG ON CATALOG <uc_catalog> TO `planners`;
      GRANT USE SCHEMA, SELECT ON SCHEMA <uc_catalog>.<uc_schema> TO `planners`;
      ```
- [ ] **Genie space** — `CAN VIEW` / run (share the space with the users/group)
- [ ] **Vector Search index** — `SELECT` on `<uc_catalog>.<uc_schema>.knowledge_chunks_index`
- [ ] **SQL warehouse** — `CAN USE` (Genie + operational queries execute as the user)
- [ ] ❌ **No Lakebase grant needed** — the operational agent connects as the App SP and scopes
      rows with an in-query access predicate (full per-user Lakebase RLS is a later phase).

---

## 7. Bring your own data (instead of the demo samples)

The bundle is demo-first. To point at your own governed tables:
1. Set `uc_catalog` / `uc_schema` to your data's location.
2. Deploy **without** seeding: `make deploy PROFILE=<p> SEED=false`.

Nothing is overwritten — the App reads whatever the variables point at. Your tables should match
the operational schema the agent expects (see [`data/genie/genie_config.py`](../data/genie/genie_config.py)).

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/ui` returns "SPA not built" | `frontend/dist` wasn't shipped. Run `make deploy` (it builds first), not a raw `bundle deploy`. |
| App `password authentication failed` (Lakebase) | App SP has no Postgres role → run `scripts/grant_app_sp.sh` (or add the role in the Lakebase UI). |
| Knowledge route errors "VECTOR_SEARCH_INDEX not set" | The `build_vs_index` seed task didn't finish, or `uc_catalog`/`uc_schema` mismatch. Re-run `make seed`. |
| Analytics/Genie route says no space | `genie_space_id` still blank → set it from the `create_genie_space` task output + redeploy (§5 step 4). |
| Traces don't appear | `sql_warehouse_id` blank (tracing off), or the App SP lacks the trace-schema grants in §6.B. |
| Seed task seeds the wrong catalog | The task config comes from `databricks.yml` task parameters → `agent_server.config`. Check the task's run parameters/widgets in the job UI match your `uc_catalog`. |
| `create-catalog` for Lakebase fails on CLI | Known autoscaling CLI bug — register the catalog in the **UI** (§3). |

---

## 9. Tear down

```bash
make destroy PROFILE=<p>     # removes the App, experiment, and job
```
This does **not** delete your Lakebase project, your UC catalog/data, the Genie space, or the
Vector Search index — remove those manually if you want a clean slate.
