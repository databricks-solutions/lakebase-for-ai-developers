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
| `genie_space_id` | `unset` (sentinel) | leave as-is for the first deploy; set the real id **after** the seed creates the space (§5, post-deploy step 2). The app treats `unset` as no-Genie. |
| `sql_warehouse_id` | `unset` (sentinel) | **set a real warehouse id to enable MLflow tracing + 👍/👎** (UC trace storage needs a warehouse). Leave `unset` = tracing off. Also grant the App SP `CAN USE` on it + the §6.B catalog grants. |

> Bundle variables that feed app env vars must **never default to an empty string** — DABs drops
> empty values and the Apps API rejects the resulting name-only entry. That's why `genie_space_id`
> uses the `unset` sentinel, and why there's no `sql_warehouse_id` var (UC tracing works without a
> pinned warehouse; to pin one, add `MLFLOW_TRACING_SQL_WAREHOUSE_ID` to the app env explicitly).

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

> **Whatever you pass as `lakebase_project` here, pass the same to `grant_app_sp.sh`** in the
> post-deploy step (`LAKEBASE_PROJECT=<your-project-id> …`) — otherwise it targets the default
> project and errors with *Project 'projects/mfg-supply-chain-copilot' not found*.

The seed job is **fully serverless and self-contained** — every data script runs through the
`data/_seed_task.py` launcher (which fixes `sys.path` + injects config, since serverless tasks
get no env vars and no `__file__`), the bundle creates all schemas it needs (including the Lakebase
`public` schema), and `sync_to_lakebase` creates the synced tables via the **REST API** (the
`databricks` CLI is blocked on serverless compute). No manual schema/table creation is required.

That wrapper runs four steps (raw equivalents are in the header of `databricks.yml`):
1. `npm --prefix frontend ci && npm --prefix frontend run build` → `frontend/dist`
2. `databricks bundle deploy -t dev --profile <p>` → uploads source + creates the app **object**, experiment, job
3. `databricks bundle run supply_chain_planner -t dev --profile <p>` → **deploys the app** (creates the active deployment that points it at the source and makes it live)
4. `databricks bundle run setup_and_seed -t dev --profile <p>` → loads demo data

> **Why step 3 is separate:** `bundle deploy` does *not* deploy an app — it only creates the app
> object (the shell). The app stays "No source code / Unavailable" until `bundle run <app-key>`
> (= `apps deploy`) creates a deployment. `make deploy` does this for you; if you ever run the raw
> commands, don't skip it.

Then the **two post-deploy steps** (these can't be DABs resources):

1. **Grant the App's service principal a Lakebase Postgres role** (the App SP only exists after the
   first deploy, so this is a follow-up):
   ```bash
   PROFILE=<p> ./scripts/grant_app_sp.sh
   # If you renamed the Lakebase project, pass it (must match the bundle's lakebase_project):
   #   LAKEBASE_PROJECT=mfg-supply-chain-copilot-test PROFILE=<p> ./scripts/grant_app_sp.sh
   ```
   The script resolves the App SP, registers it as a Postgres role, and runs the GRANTs. It reads
   `LAKEBASE_PROJECT` / `LAKEBASE_BRANCH` / `LAKEBASE_ENDPOINT` from env (defaults match §3); or add
   the role via the Lakebase **UI** (instance → Roles) and re-run.

2. **Wire the Genie space.** The Genie space is a carve-out — it isn't a DABs resource, so it's a
   two-phase wire-up. The first deploy ran with `genie_space_id` blank; the seed's
   `create_genie_space` task **created** a Genie space and printed its id. Grab that id and redeploy
   the app (only) with it set, so the Analytics (NL→SQL) route binds.

   **Get the id** from the `create_genie_space` task output:
   ```bash
   # find the latest setup_and_seed run, then read the create_genie_space task's output
   RUN=$(databricks jobs list-runs --profile <p> -o json \
     | python3 -c 'import sys,json;rs=[r for r in json.load(sys.stdin)["runs"] if "setup-and-seed" in r["run_name"]];print(rs[0]["run_id"])')
   databricks jobs get-run "$RUN" --profile <p> -o json \
     | python3 -c 'import sys,json;d=json.load(sys.stdin);print([t["run_id"] for t in d["tasks"] if t["task_key"]=="create_genie_space"][0])' \
     | xargs -I{} databricks jobs get-run-output {} --profile <p> -o json \
     | python3 -c 'import sys,json;print(json.load(sys.stdin).get("logs",""))' | grep -i "genie.*space\|space.*id"
   ```
   (Or open the run in the **Jobs UI → `create_genie_space` task → Output** and copy the id.)

   **Set it and redeploy the app** — pass it via `VARS` along with the same catalog/project you
   deployed with (`SEED=false` skips re-seeding; the data is already loaded):
   ```bash
   make deploy PROFILE=<p> SEED=false \
     VARS="uc_catalog=<catalog> lakebase_project=<project-id> genie_space_id=<the-id>"
   ```
   (Until set, the Analytics/Genie route degrades gracefully — every other route works.)

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
- [ ] **MLflow UC tracing** — required for traces **and the 👍/👎 feedback** to record. Needs THREE
      things: (a) `sql_warehouse_id` set to a real warehouse (UC trace storage needs one); (b) the
      App SP `CAN USE` on that warehouse; and (c) the App SP can write the trace tables:
      ```sql
      GRANT USE CATALOG ON CATALOG <uc_catalog> TO `<app-sp-client-id>`;
      GRANT USE SCHEMA, CREATE TABLE, MODIFY ON SCHEMA <uc_catalog>.<mlflow_trace_schema> TO `<app-sp-client-id>`;
      ```
      Missing any of these → the app starts fine but logs a non-fatal `UC trace binding failed …
      PERMISSION_DENIED: … USE CATALOG …` (or `UC tracing OFF: no SQL warehouse set`) and falls back
      to plain tracing, which **doesn't record on Apps** (egress-blocked) — so the experiment's
      Traces tab and any 👍/👎 stay empty. After granting, **redeploy** so setup re-runs.
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
| Analytics/Genie route says no space | `genie_space_id` still blank → set it from the `create_genie_space` task output + redeploy (§5, post-deploy step 2). |
| Traces don't appear | `sql_warehouse_id` blank (tracing off), or the App SP lacks the trace-schema grants in §6.B. |
| Seed task seeds the wrong catalog | The task config is the JSON arg the bundle passes to `data/_seed_task.py` → `os.environ` → `agent_server.config`. Check the task's run parameters in the job UI match your `uc_catalog`. |
| `sync_to_lakebase` fails: `Schema '<catalog>.public' does not exist` | `bootstrap_schemas` didn't run / a stale deploy — it creates `<uc_catalog>.public`. Re-run `make seed`. |
| `sync_to_lakebase` fails: `The Databricks CLI is only supported ... web terminal` | A stale deploy without the fix — `03` must use the REST API path (`data/operational/03_sync_to_lakebase.py` via the SDK), not the CLI. Re-deploy. |
| `bundle deploy` fails: `Cannot move node '… -traces' … because it is in trash folder` | You deleted the bundle-managed MLflow experiment but it's still in the workspace **trash**, and DABs' local state still tracks it. Fastest fix: `databricks experiments restore-experiment <id> -p <p>` then re-deploy. For a truly fresh experiment: permanently delete it (MLflow → Experiments → **Trash** → Delete permanently — UI only) **and** `rm -rf .databricks/bundle/<target>`, then re-deploy. |
| `grant_app_sp.sh`: `Project 'projects/mfg-supply-chain-copilot' not found` | The script used the default project name. Pass the one you deployed with: `LAKEBASE_PROJECT=<your-project-id> PROFILE=<p> ./scripts/grant_app_sp.sh`. |
| `bundle deploy` fails: `workspace_id mismatch` | Stale local state from a prior workspace. `rm -rf .databricks/bundle/<target>` and re-deploy (note: this also makes DABs forget the app/job/experiment it created — delete those on the workspace first if they still exist). |
| App shows **"No source code" / "No active deployment"** but Status: Active | `bundle deploy` only created the app *object*; it doesn't deploy the app. Deploy it: `databricks bundle run supply_chain_planner -t <target> --profile <p> <--var …>`. `make deploy` now runs this automatically (step 3). |
| `bundle run <app>` fails: `Must specify environment variable source using either value or valueFrom` | An app env var resolved to an **empty string** — DABs drops the value, leaving a name-only entry Apps rejects. Don't let any bundle variable that feeds app env default to `""` (use a sentinel like `unset`, or omit the env var). All current vars are fixed; if you add one, give it a non-empty default. |
| "Inspect backend" / chat shows a **502** (raw HTML) right after deploy | Cold start — the worker is still warming behind the proxy (`/api/*` is briefly unavailable). The UI retries automatically; just wait ~15–30s and retry. Only a 502 that persists once the app is **warm** is a real error (check Apps UI → Logs). |
| **No traces / 👍👎 in the experiment** (Traces tab + UC trace tables empty) | UC tracing isn't enabled. Need all of: `sql_warehouse_id` set to a real warehouse; App SP `CAN USE` that warehouse; and the §6.B `USE CATALOG` + trace-schema grants. App logs `UC trace binding failed … PERMISSION_DENIED … USE CATALOG` or `UC tracing OFF: no SQL warehouse set`. Fix, then **redeploy** (setup runs at startup). Non-fatal — the app still works. |
| Clicking a **historical chat** opens an empty panel | Either that chat predates this build (transcripts persist going forward only), or the store read failed (check logs). New chats persist + rehydrate automatically. |

---

## 9. Tear down

```bash
make destroy PROFILE=<p>     # removes the App, experiment, and job
```
This does **not** delete your Lakebase project, your UC catalog/data, the Genie space, or the
Vector Search index — remove those manually if you want a clean slate.
