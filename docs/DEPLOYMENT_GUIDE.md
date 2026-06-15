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
| Backend explorer · **persistent per-user chat history** (reopen past chats; title inferred from the first message) · 👍/👎 feedback | same App | `/api/*` — history + transcripts persist in the Lakebase store; feedback logs to the run's trace |
| Demo dataset (operational tables, pgvector, Genie space, Vector Search index) | `jobs.setup_and_seed` | Unity Catalog + Lakebase |
| MLflow experiment (tracing target) | `experiments.planner_experiment` | Fresh per deploy |

There is **no separate frontend deployment** — the App process serves the agent, your `/api/*`
routes, and the compiled UI together. The build step (`npm run build`) produces `frontend/dist`,
which the bundle ships via `sync.include`.

---

## 2. Prerequisites

### Tooling (your laptop)
- [ ] **Databricks CLI** ≥ 1.3.0 (`databricks -v`) — `deploy.sh` enforces this in preflight. Required
      for the `resources.genie_spaces` resource + the **direct** deployment engine (GA + default since
      1.3.0); it also covers the native `postgres` app resource (added in 0.294). Older CLIs fail the deploy.
- [ ] **Node.js 18+** and npm (builds the SPA)
- [ ] **uv** (`pip install uv`) — used by the App runtime and local runs

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
in Lakebase autoscaling Postgres.

> **`make deploy` ensures the project exists for you** (idempotent) — `deploy.sh` phase 1 runs
> `scripts/ensure_lakebase_project.py` before the bundle deploy, so the native `postgres` app
> resource always has a project/branch/database to bind to. The manual create below is still useful
> if you want to pre-create the project or pick non-default names.

Create a project (UI: **Compute → Lakebase → Create**, or the `databricks-lakebase-autoscale`
skill / CLI):

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

> **You need `CAN MANAGE` on the Lakebase project** — the deploy attaches a native `postgres` app
> resource to it (which auto-registers the App SP's Postgres role + grants CONNECT/CREATE), and
> attaching the resource requires `CAN MANAGE`. Workspace admins have it; otherwise grant it on the
> project. See §6.A.

That's the only Lakebase prerequisite. You do **not** need to register the Lakebase database as a
separate UC catalog — the operational synced tables land in **`uc_catalog`** (schema `public`), and
the bundle's `bootstrap_schemas` task creates that schema for you. The synced-table spec carries its
own `branch` + `postgres_database`, so a plain writable catalog is all it needs.

---

## 4. Configure the bundle

Everything workspace-specific is a variable in [`databricks.yml`](../databricks.yml). Set the ones
that differ from the defaults — simplest is to edit the `default:`s once, or pass `--var` flags.

| Variable | Default | Set to |
|---|---|---|
| **`uc_catalog`** ← the one you must set | `main` | **a catalog you can write to** — `main` usually doesn't exist on a fresh workspace, so this is the one variable you'll almost always override. Holds the Delta tables, Knowledge volume, VS index, trace schema, **and** the operational synced tables (schema `public`). |
| `uc_schema` | `supply_chain_planner` | schema for the demo tables |
| `lakebase_project` / `_branch` / `_endpoint` / `_database` | `mfg-supply-chain-copilot` / `production` / `primary` / `databricks_postgres` | your Lakebase coordinates |
| `vector_search_endpoint` | `supply-chain-planner-vs` | VS endpoint name (created by the seed job) |
| `embedding_endpoint` | `databricks-gte-large-en` | a Databricks embedding endpoint |
| `llm_endpoint` | `databricks-claude-opus-4-8` | a Foundation Model endpoint |
| `genie_consumer_group` | `users` | workspace group granted `CAN_RUN` on the bundle-created Genie space (OBO consumers). Scope tighter with `--var genie_consumer_group=<group>` or `GENIE_GROUP=<group>`. |
| `sql_warehouse_id` | the **bundle-created** `app_sql_warehouse` (small serverless wh) | leave as-is — the bundle creates + binds the warehouse (App SP auto-granted `CAN USE`; trace-schema grants run in the seed). Pass `--var sql_warehouse_id=<id>` to BYO an existing/governed warehouse. |

> Bundle variables that feed app env vars must **never default to an empty string** — DABs drops
> empty values and the Apps API rejects the resulting name-only entry. The Genie and warehouse ids
> sidestep this the same way: both reach the app through a **resource binding** (`value_from`), not a
> raw env value. `GENIE_SPACE_ID` comes from the bundle-created `genie_spaces` resource and
> `MLFLOW_TRACING_SQL_WAREHOUSE_ID` from the `sql_warehouse` binding — so there's always a real id
> (and the App SP gets `CAN_RUN` / `CAN USE` respectively).

---

## 5. Deploy

```bash
# 0. authenticate the CLI to the target workspace
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile <p>

# 1. one command: build the SPA, deploy the bundle, seed the demo data.
#    Pass workspace-specific variables via VARS (no need to edit databricks.yml):
make deploy PROFILE=<p> VARS="uc_catalog=<your-writable-catalog>"

#    Override any other default the same way — e.g. if you named the Lakebase project differently:
make deploy PROFILE=<p> VARS="uc_catalog=<catalog> lakebase_project=<your-project-id>"
```

The seed job is **fully serverless and self-contained** — every data script runs through the
`data/_seed_task.py` launcher (which fixes `sys.path` + injects config, since serverless tasks
get no env vars and no `__file__`), the bundle creates all schemas it needs (including the Lakebase
`public` schema), and `sync_to_lakebase` creates the synced tables via the **REST API** (the
`databricks` CLI is blocked on serverless compute). No manual schema/table creation is required.

The App SP's Lakebase access is **fully automatic** — there is no manual grant step:
- The native **`postgres` app resource** (in `databricks.yml`) makes the platform register the SP's
  Postgres role and grant it `CONNECT` + `CREATE` on the database, so the SP self-creates and owns
  its agent-memory + write-back schemas at startup.
- The seed's **`grant_app_sp` task** then `GRANT`s the SP `USAGE` + `SELECT` on the synced `public`
  tables (the one thing the resource can't — those tables are platform-owned). It runs **after**
  `sync_to_lakebase` and after the app is deployed, so the tables and the SP both already exist.

That wrapper ([`scripts/deploy.sh`](../scripts/deploy.sh)) runs these phases — critical phases fail
fast; seed/Genie/verify **degrade gracefully** so a partial failure still leaves a working core app:
0. **Preflight** — CLI ≥ 1.3.0, authenticated profile, `uc_catalog` exists, `node`/`uv` present; prints the Genie+OBO manual-steps banner.
1. **Lakebase project** — `scripts/ensure_lakebase_project.py` ensures the autoscaling project (idempotent).
2. **Build** — `npm --prefix frontend ci && run build` → `frontend/dist`; then generate `data/genie/supply_chain.geniespace.json` from `genie_config.py`.
3. **`bundle deploy`** — uploads source + creates the app **object**, experiment, job, **and the Genie space** (`resources.genie_spaces`, bound to the app).
4. **`bundle run supply_chain_planner`** — **deploys the app** (creates the active deployment that makes it live; also when the `postgres` resource registers the SP's Postgres role).
5. **`bundle run setup_and_seed`** — loads demo data + the `grant_app_sp` SELECT grant (unless `SEED=false`).
6. **Verify + report** — runs `scripts/verify_deploy.py`, waits for the app to be ACTIVE, prints the URL.

> **Why deploying the app is a separate step (4):** `bundle deploy` does *not* deploy an app — it
> only creates the app object (the shell). The app stays "No source code / Unavailable" until
> `bundle run <app-key>` (= `apps deploy`) creates a deployment. `deploy.sh` does this for you; if
> you ever run the raw commands, don't skip it.

**Fast dev loop** — once deployed, iterate without re-seeding (stays on the same target/app, never deletes it):
```bash
make redeploy    PROFILE=<p>   # agent-server code change → bundle deploy + bundle run (~30-60s)
make redeploy-ui PROFILE=<p>   # frontend change → npm build + deploy + run
```

### Genie is created as code — but OBO has two manual, security-gated steps

The Genie space is a first-class DABs resource (`resources.genie_spaces`), created + bound to the app
in a single `bundle deploy` — no capture-the-id-and-redeploy. Two things the deploy **cannot** do for
you (surfaced in the preflight banner):
1. A **workspace admin** enables the **"Databricks Apps – On-Behalf-Of-User Authorization"** Public Preview.
2. **Each end user accepts the OAuth consent** on first open (a stale browser session → 403
   `invalid scope`; re-open in a fresh/incognito session). End users also need `CAN USE` on a
   serverless/pro warehouse and `SELECT` on the underlying tables. (`CAN_RUN` on the space is granted
   to `users` by default — `GENIE_GROUP=<group>` to scope tighter.)

Until those are done the Analytics/Genie route degrades gracefully — every other route works.

### Verify
```bash
databricks apps list --profile <p>     # supply-chain-planner → RUNNING; copy the URL
```
Open the App URL `/ui`, sign in, and ask:
*"Have we seen a disruption like the PrecisionBond recall before?"* → you should get a traced
answer, and 👍/👎 should attach as an assessment in the experiment.

> **Give it ~15–30s after deploy.** The app warms up behind the Databricks Apps proxy; the static
> UI loads immediately but the first dynamic `/api/*` calls can briefly return a **502** during
> that window. The chat and the "Inspect backend" drawer retry automatically — if you see a 502,
> wait a moment and retry. A *persistent* 502 (after the app is warm) is a real error → check
> **Apps UI → your app → Logs** (the `databricks apps logs` CLI only shows a startup snapshot).

**Reopen a past chat:** start a conversation, reload the page, then click it under HISTORY — it
rehydrates the full transcript (persisted in the Lakebase store) and is named after your first
message. *Transcripts persist going forward only* — chats created before this build won't reopen.

---

## 6. Permissions checklist

Three identities are involved. **The bundle + seed automate the App-SP grants** (the experiment,
the Lakebase role/CONNECT/CREATE via the `postgres` resource, and the operational SELECT via the
`grant_app_sp` seed task); the rest are listed with the exact commands. Minimal-permission by
design: **user-facing data reads run on-behalf-of-user (OBO)**, so the App SP needs *no* grant on
Genie, the VS index, or the user's UC tables — only the user does.

### A. You — the deployer (runs `make deploy`)
- [ ] **Databricks CLI ≥ 0.294** — required for the native `postgres` app resource (see §2)
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
- [ ] **Lakebase** — create a project (§3) — workspace admin or the Lakebase entitlement, **and**
      **`CAN MANAGE` on the Lakebase project** so the deploy can attach the `postgres` app resource
      (which auto-registers the App SP's Postgres role). Workspace admins have this.
- [ ] **Vector Search** — create an endpoint + index — VS entitlement + `CREATE` on `<uc_catalog>.<uc_schema>`
- [ ] **Genie** — create a Genie space — Genie entitlement + `CAN MANAGE` on a SQL warehouse
- [ ] **SQL warehouse** — the bundle **creates** one (`app_sql_warehouse`) for tracing + the seed's
      Genie/VS SQL, so the deployer needs the entitlement to **create a serverless SQL warehouse**
      (workspace admins have it). BYO instead with `--var sql_warehouse_id=<id>` (then you just need
      `CAN USE` on that one); a deployer who can create neither is the byo/Tier-2 case.

### B. The App service principal (auto-created on first deploy)
Resolve its id: `databricks apps get supply-chain-planner -p <p> -o json` → `service_principal_client_id`.

- [x] **MLflow experiment `CAN_MANAGE`** — **automated by the bundle** (the `experiment`
      app-resource in `databricks.yml`); the SP writes traces here
- [x] **Lakebase role + `CONNECT` + `CREATE`** — **automated by the `postgres` app-resource** in
      `databricks.yml`: the platform registers the SP's Postgres role (role name == its client-id
      UUID) and grants CONNECT/CREATE on the database, so the SP self-creates + owns its
      **agent-memory schema** (LangGraph checkpoint + store) and its **write-back schema**
      `supply_chain_planner_app` (`approved_actions` / `planning_parameters` / `constraints`) at
      startup. *No manual step.* (Rare fallback if the role hasn't propagated: SQL
      `SELECT databricks_create_role('<app-sp-client-id>','SERVICE_PRINCIPAL');` on the branch.)
- [x] **Lakebase `USAGE` + `SELECT` on `public` (the synced operational tables)** — **automated by
      the seed's `grant_app_sp` task** (a superuser GRANT — the synced read tables are owned by the
      platform's `databricks_writer_*` role, not the SP, so the resource can't cover them). Runs
      after `sync_to_lakebase`, re-resolving the SP each run.
- [ ] **Foundation Model endpoints** `CAN QUERY` on `<llm_endpoint>` **and** `<embedding_endpoint>`
      — the planner/router and the long-term-memory store run as the App SP
      (usually granted to all principals by default; verify in *Serving → endpoint → Permissions*)
- [ ] **MLflow UC tracing** — records traces **and the 👍/👎 feedback**. With the bundle defaults
      this is **automatic**: (a) the bundle creates `app_sql_warehouse` and binds it to the app, so the
      App SP gets `CAN USE` on the warehouse; and (b) the seed's `grant_app_sp` task grants the App SP
      the trace-table privileges:
      ```sql
      GRANT USE CATALOG ON CATALOG <uc_catalog> TO `<app-sp-client-id>`;
      GRANT USE SCHEMA, CREATE TABLE, MODIFY, SELECT ON SCHEMA <uc_catalog>.<mlflow_trace_schema> TO `<app-sp-client-id>`;
      ```
      If you BYO a warehouse (`--var sql_warehouse_id=<id>`) the binding still grants `CAN USE`. If
      anything is missing, the app starts fine but logs a non-fatal `UC trace binding failed …
      PERMISSION_DENIED: … USE CATALOG …` (or `UC tracing OFF: no SQL warehouse set`) and falls back
      to plain tracing, which **doesn't record on Apps** (egress-blocked) — so the experiment's
      Traces tab and any 👍/👎 stay empty. After fixing, **redeploy** so setup re-runs.
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
- [ ] ❌ **No Lakebase grant needed** — the operational agent connects as the App SP, so every
      authenticated app user sees the same UC-governed data (no per-user row scoping; per-user
      scoping in production would be Postgres RLS or an entitlements join, not an app-side ACL).

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
| App `password authentication failed` / `role "<uuid>" does not exist` (Lakebase) | The `postgres` app resource didn't register the SP's Postgres role — confirm the resource is attached (`databricks.yml` app `resources:` has the `postgres` block) and the deployer has **`CAN MANAGE` on the Lakebase project** (§6.A), then **redeploy the app** (`databricks bundle run supply_chain_planner -t <target> -p <p>`). Rare fallback: add the role in the Lakebase UI or run SQL `SELECT databricks_create_role('<app-sp-client-id>','SERVICE_PRINCIPAL');`. |
| App can't **read the synced tables** / `permission denied for table inventory_current` (or other `public.*`) | The `grant_app_sp` seed task didn't run, or the SP lacks `SELECT` on `public`. The `postgres` resource grants CONNECT/CREATE but **not** SELECT on platform-owned synced tables — that's the `grant_app_sp` task's job (depends on `sync_to_lakebase`). Re-run `make seed` (check the `grant_app_sp` task succeeded in the job UI). |
| App can't **create its schema** at startup / `permission denied for database` (memory or write-back schema) | The `postgres` app resource isn't attached, or `lakebase_database_resource` is wrong so the resource bound to the wrong database (no CREATE granted). Verify the internal resource name: `databricks api get /api/2.0/postgres/projects/<p>/branches/<b>/databases` (deterministic kebab-case of the db name, e.g. `databricks-postgres`), set `lakebase_database_resource` to match, and redeploy. |
| Knowledge route errors "VECTOR_SEARCH_INDEX not set" | The `build_vs_index` seed task didn't finish, or `uc_catalog`/`uc_schema` mismatch. Re-run `make seed`. |
| Analytics/Genie route says no space | The `genie_spaces` resource didn't deploy (or `GENIE_SPACE_ID` env is empty). Confirm the CLI is ≥ 1.3.0 on the **direct** engine, `databricks bundle plan` shows the space + the app's `genie-space` binding resolved, then redeploy. |
| Traces don't appear | UC trace bind failed at startup. Check app logs for `UC trace binding failed … PERMISSION_DENIED … USE CATALOG` (seed's trace-schema grants didn't run → re-run `make seed`) or `UC tracing OFF: no SQL warehouse set` (a BYO `--var sql_warehouse_id` was empty/`unset`). The bundle default creates + binds the warehouse, so this is rare. |
| Seed task seeds the wrong catalog | The task config is the JSON arg the bundle passes to `data/_seed_task.py` → `os.environ` → `agent_server.config`. Check the task's run parameters in the job UI match your `uc_catalog`. |
| `sync_to_lakebase` fails: `Schema '<catalog>.public' does not exist` | `bootstrap_schemas` didn't run / a stale deploy — it creates `<uc_catalog>.public`. Re-run `make seed`. |
| `sync_to_lakebase` fails: `The Databricks CLI is only supported ... web terminal` | A stale deploy without the fix — `03` must use the REST API path (`data/operational/03_sync_to_lakebase.py` via the SDK), not the CLI. Re-deploy. |
| `bundle deploy` fails: `Cannot move node '… -traces' … because it is in trash folder` | You deleted the bundle-managed MLflow experiment but it's still in the workspace **trash**, and DABs' local state still tracks it. Fastest fix: `databricks experiments restore-experiment <id> -p <p>` then re-deploy. For a truly fresh experiment: permanently delete it (MLflow → Experiments → **Trash** → Delete permanently — UI only) **and** `rm -rf .databricks/bundle/<target>`, then re-deploy. |
| `bundle deploy` fails: `workspace_id mismatch` | Stale local state from a prior workspace. `rm -rf .databricks/bundle/<target>` and re-deploy (note: this also makes DABs forget the app/job/experiment it created — delete those on the workspace first if they still exist). |
| App shows **"No source code" / "No active deployment"** but Status: Active | `bundle deploy` only created the app *object*; it doesn't deploy the app. Deploy it: `databricks bundle run supply_chain_planner -t <target> --profile <p> <--var …>`. `make deploy` now runs this automatically (step 4). |
| `bundle run <app>` fails: `Must specify environment variable source using either value or valueFrom` | An app env var resolved to an **empty string** — DABs drops the value, leaving a name-only entry Apps rejects. Don't let any bundle variable that feeds app env default to `""` (use a sentinel like `unset`, or omit the env var). All current vars are fixed; if you add one, give it a non-empty default. |
| "Inspect backend" / chat shows a **502** (raw HTML) right after deploy | Cold start — the worker is still warming behind the proxy (`/api/*` is briefly unavailable). The UI retries automatically; just wait ~15–30s and retry. Only a 502 that persists once the app is **warm** is a real error (check Apps UI → Logs). |
| **No traces / 👍👎 in the experiment** (Traces tab + UC trace tables empty) | UC tracing isn't binding. The bundle default handles all three pieces — the warehouse (created), App SP `CAN USE` (the `trace-warehouse` app binding), and the `USE CATALOG` + trace-schema grants (seed's `grant_app_sp`). So this means one didn't apply: check app logs for `UC trace binding failed … PERMISSION_DENIED … USE CATALOG` (re-run `make seed`) or `UC tracing OFF: no SQL warehouse set` (BYO `--var sql_warehouse_id` empty). Fix, then **redeploy**. Non-fatal — the app still works. Also: the UC destination only binds to a **trace-free** experiment, so re-homing tracing needs a fresh experiment (bump the `planner_experiment_uc` resource **key**). |
| Clicking a **historical chat** opens an empty panel | Either that chat predates this build (transcripts persist going forward only), or the store read failed (check logs). New chats persist + rehydrate automatically. |

---

## 9. Tear down

```bash
make destroy PROFILE=<p>     # removes the App, experiment, and job
```
This does **not** delete your Lakebase project, your UC catalog/data, the Genie space, or the
Vector Search index — remove those manually if you want a clean slate.
