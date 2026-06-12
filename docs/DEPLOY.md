# Deploy — one-shot DABs bundle

> **New workspace?** Read the full walkthrough — including the **permissions checklist** — in
> [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md). This page is the quick reference.


Stand up the whole demo on **your own workspace** with one command: the App (LangGraph agent +
chat UI at `/ui`), a fresh MLflow experiment for tracing, and a setup-and-seed job that loads the
demo dataset (operational tables + pgvector + Genie + Knowledge/Vector-Search).

Everything workspace-specific is a bundle **variable** (`databricks.yml`), so the same bundle
deploys anywhere. Start on the **bundled demo samples**; point at your **own data** later.

## Prerequisites (one-time)

You should be a **workspace admin** on the target workspace. Then:

1. **CLI profile** → your workspace:
   ```bash
   databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile <p>
   ```
2. **Lakebase autoscaling project** (the agent's durable state + operational hybrid query). Create
   one (UI: *Compute → Lakebase*, or the `databricks-lakebase-autoscale` skill). Note its
   project id / branch / endpoint and set the `lakebase_*` variables if they differ from the
   defaults (`mfg-supply-chain-copilot` / `production` / `primary`).
3. **A UC catalog you can write to** — set `uc_catalog`. (New catalogs can be blocked by Default
   Storage; reuse an existing one like `main` or a sandbox catalog if so.)
4. **Node 18+** locally (the SPA build) and `uv` (already used by the repo).
5. *(optional, for tracing)* a **SQL warehouse** — set `sql_warehouse_id` to enable MLflow UC
   tracing. Leave blank to skip.

Set non-default variables either inline (`--var uc_catalog=main`) or in a `*.tfvars`-style block;
simplest is to edit the `default:`s in `databricks.yml` for your workspace once.

## Deploy

```bash
make deploy PROFILE=<p>                 # full one-shot (build, deploy, seed, auto-wire Genie, verify)
make deploy PROFILE=<p> SEED=false      # bring your own data — skip seeding
make deploy PROFILE=<p> TARGET=demo     # clean prod-style resource names (default target: dev)
make deploy PROFILE=<p> GENIE_GROUP=<g> # also grant a workspace group CAN_RUN on the Genie space (OBO)
```

`make deploy` is a thin wrapper over [`scripts/deploy.sh`](../scripts/deploy.sh) — one idempotent,
cold-start-safe engine. Phases: **0** cold-start preflight (CLI ≥ 0.295, auth, catalog exists,
node/uv) → **1** ensure the Lakebase project → **2** build the SPA → **3** `bundle deploy` → **4**
`bundle run` (the active deployment — `bundle deploy` only makes the shell) → **5** seed → **6**
Genie wire-up (create/find the space, optional group grant, capture the id, redeploy) → **7** verify
+ print the app URL. Critical phases fail fast; seed/Genie/verify **degrade gracefully** so a partial
failure still leaves a working core app.

### Fast dev loop (after the first full deploy)

Iterating on code? Skip the seed/Genie/lakebase steps — just push code and restart the app:

```bash
make redeploy    PROFILE=<p>   # agent-server (Python) change: bundle deploy + bundle run (~30-60s)
make redeploy-ui PROFILE=<p>   # frontend change: npm build + bundle deploy + bundle run
```

These stay on the same target/app and **never delete it**, so the app SP and its Lakebase schemas
persist (only app *delete + recreate* orphans schemas — see
[`lakebase-apps-permissions.md`](lakebase-apps-permissions.md)).

## The data toggle

- **Demo samples (default).** `SEED=true` runs the seed job → operational tables, pgvector,
  Genie space, and the Knowledge VS index, all in `uc_catalog.uc_schema`. Works out of the box.
- **Your own data.** Re-point `uc_catalog` / `uc_schema` at your governed tables and deploy with
  `SEED=false`. The app reads whatever the variables point at; nothing is overwritten. (Your
  tables should match the operational schema the agent expects — see `data/genie/genie_config.py`.)

## What used to be manual is now automatic

Two follow-ups that used to be hand-run are folded into `deploy.sh`:

- **App service-principal Lakebase access** — the native `postgres` app resource grants the SP
  `CONNECT` + `CREATE`, the SP self-creates + owns its memory/write-back schemas at startup, and the
  seed's `grant_app_sp` task adds the `SELECT` grant on the synced `public` tables. No manual
  `grant_app_sp.sh` step (that script is gone).
- **Genie wiring** — `deploy.sh` creates/finds the space, captures its id, patches `genie_space_id`
  into `databricks.yml`, and redeploys. Pass `GENIE_GROUP=<group>` to also grant that group `CAN_RUN`.

**Two Genie+OBO steps the deploy genuinely can't do** (security-gated — surfaced in the preflight banner):

1. A **workspace admin** enables the **"Databricks Apps – On-Behalf-Of-User Authorization"** Public Preview.
2. **Each end user accepts the OAuth consent** on first open (a stale browser session → 403
   `invalid scope`; re-open in a fresh/incognito session). End users also need `CAN_RUN` on the space
   (use `GENIE_GROUP`), `CAN USE` on a serverless/pro warehouse, and `SELECT` on the underlying tables.

Until those are done the **Analytics/Genie route degrades gracefully** and every other route works.

> **Don't delete the app to "start clean."** Redeploy in place (`make deploy` / `make redeploy`).
> Deleting the app destroys its service principal and orphans the Lakebase schemas it owns. If you
> must recreate, detach the Lakebase resource as `CAN MANAGE` first — see
> [`lakebase-apps-permissions.md`](lakebase-apps-permissions.md).

If you registered the Lakebase database as a UC catalog via CLI and it failed, register it in the
UI (*Catalog Explorer → Create catalog → from a Lakebase database*) — the autoscaling
`create-catalog` CLI path has a known body-stripping bug.

## Verify

```bash
databricks apps list --profile <p>        # supply-chain-planner → RUNNING, note the URL
```

Open the app URL `/ui`, ask *"Have we seen a disruption like the PrecisionBond recall before?"*,
and confirm a traced answer. Traces land in the bundle's experiment
(`/Users/<you>/supply_chain_planner-<target>-traces`); 👍/👎 attach as assessments.

## Tear down

```bash
make destroy PROFILE=<p>      # removes the App, experiment, and job (NOT the Lakebase project or your catalog)
```
