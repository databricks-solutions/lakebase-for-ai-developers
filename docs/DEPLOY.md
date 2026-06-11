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
make deploy PROFILE=<p>                 # build SPA, deploy bundle, seed demo data (default)
make deploy PROFILE=<p> SEED=false      # bring your own data — skip seeding
make deploy PROFILE=<p> TARGET=demo     # clean prod-style resource names (default target: dev)
```

`make deploy` runs three things: `npm run build` (SPA → `frontend/dist`, shipped via
`sync.include`), `databricks bundle deploy`, then `databricks bundle run setup_and_seed` unless
`SEED=false`. Raw equivalents are in the header of `databricks.yml`.

## The data toggle

- **Demo samples (default).** `SEED=true` runs the seed job → operational tables, pgvector,
  Genie space, and the Knowledge VS index, all in `uc_catalog.uc_schema`. Works out of the box.
- **Your own data.** Re-point `uc_catalog` / `uc_schema` at your governed tables and deploy with
  `SEED=false`. The app reads whatever the variables point at; nothing is overwritten. (Your
  tables should match the operational schema the agent expects — see `data/genie/genie_config.py`.)

## Two post-deploy steps (carve-outs)

These can't be clean DABs resources, so they're explicit one-time follow-ups:

1. **Grant the App's service principal a Lakebase Postgres role** so the app can connect. The app
   SP only exists after the first deploy. Run:
   ```bash
   scripts/grant_app_sp.sh <p>      # registers the SP's Lakebase role + GRANTs (see the script)
   ```
2. **Wire the Genie space.** The seed job's `create_genie_space` task creates a Genie space and
   prints its id. Set `genie_space_id` to that value and re-run `make deploy` so the Analytics
   agent binds to it. (Until then the Analytics agent degrades gracefully — other routes work.)

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
