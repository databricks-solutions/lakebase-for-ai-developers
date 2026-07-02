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
   Re-run this if the profile already exists but its OAuth refresh token expired (`A new access
   token could not be retrieved because the refresh token is invalid`) — `deploy.sh` preflight
   catches this and tells you which profile to re-auth.
2. **`serverless_compute_id = auto`** in `[<profile>]` in `~/.databrickscfg` — phase 3
   (`create_operational_tables`) runs Databricks Connect **locally** and needs either this or a
   `cluster_id`. Profiles created via `databricks auth login` don't set this by default; add it by
   hand once per profile. Preflight warns if it's missing.
3. **Lakebase autoscaling project** (the agent's durable state + operational hybrid query). Create
   one (UI: *Compute → Lakebase*, or the `databricks-lakebase-autoscale` skill). Note its
   project id / branch / endpoint and set the `lakebase_*` variables if they differ from the
   defaults (`mfg-supply-chain-copilot` / `production` / `primary`).
4. **A UC catalog you can write to** — set `uc_catalog`. **The committed `databricks.yml` default
   is workspace-specific** (it targets the catalog on the workspace this demo was originally built
   against) — on any other workspace it won't exist, and preflight now fails fast rather than
   discovering it two phases later. Pass `--uc-catalog <name>` / `--var uc_catalog=<name>`, or reuse
   an existing catalog like `main` if new-catalog creation is blocked by Default Storage.
5. **Node 18+** locally (the SPA build) and `uv` (already used by the repo).
6. *(tracing — automatic)* on the `dev`/`demo` targets the bundle **creates a small serverless SQL
   warehouse** for MLflow UC tracing + the Genie space and binds it to the app (the App SP is
   auto-granted `CAN USE`). This needs the deployer to be able to create a SQL warehouse. **If you
   can't** (restricted workspace), use the `byo` target, which omits that resource — see *Bring your
   own warehouse* below. `byo` needs an **existing SQL warehouse you have `CAN USE` on**
   (`databricks warehouses list --profile <p>` to find one).

Set non-default variables either inline (`--var uc_catalog=main`) or in a `*.tfvars`-style block;
simplest is to edit the `default:`s in `databricks.yml` for your workspace once.

## Deploy

```bash
make deploy PROFILE=<p>                 # full one-shot (build, deploy, seed, verify)
make deploy PROFILE=<p> SEED=false      # bring your own data — skip seeding
make deploy PROFILE=<p> TARGET=demo     # clean prod-style resource names (default target: dev)
make deploy PROFILE=<p> GENIE_GROUP=<g> # also grant a workspace group CAN_RUN on the Genie space (OBO)
```

Point the catalog/schema/warehouse at your workspace inline (no `databricks.yml` edit needed) — these
are the variables you'll most often override:

```bash
make deploy PROFILE=<p> VARS="uc_catalog=main uc_schema=planner"
# or, calling deploy.sh directly, the same three have first-class flags:
./scripts/deploy.sh -p <p> --uc-catalog main --uc-schema planner --sql-warehouse-id <id>
```

### Bring your own warehouse (restricted workspaces)

The `dev`/`demo` targets create their own serverless warehouse. If the deployer **lacks
warehouse-create entitlement**, that step 403s. Use the **`byo` target**, which omits the warehouse
resource — you must supply an existing warehouse id (the deployer needs `CAN USE` on it):

```bash
make deploy PROFILE=<p> TARGET=byo VARS="sql_warehouse_id=<existing-id>"
# or:  ./scripts/deploy.sh -p <p> -t byo --sql-warehouse-id <existing-id>
```

The Genie space, trace storage, and seed SQL all run against that warehouse; the App SP still gets
`CAN USE` via the app's `trace-warehouse` binding. Everything else (cold-start table DDL, Genie,
seed) is identical to `dev`. deploy.sh fails fast if you select `byo` without a warehouse id.

`make deploy` is a thin wrapper over [`scripts/deploy.sh`](../scripts/deploy.sh) — one idempotent,
cold-start-safe engine. Phases: **0** cold-start preflight (CLI ≥ 1.3.0, auth, catalog exists,
node/uv) → **1** ensure the Lakebase project → **2** build the SPA + generate the Genie-space JSON →
**3** create the operational schema + **empty** tables (so the next phase passes) → **4** `bundle
deploy` (creates the Genie space as a `genie_spaces` resource + binds it to the app) → **5** `bundle
run` (the active deployment — `bundle deploy` only makes the shell) → **6** seed → **7** verify +
print the app URL. Critical phases fail fast; seed/verify **degrade gracefully** so a partial
failure still leaves a working core app.

> **Why phase 3 (create empty tables) exists.** The `genie_spaces` resource's create-API
> **validates that its referenced tables exist** at `bundle deploy` time — but the seed (phase 6),
> which fills them, runs *after*. On a fresh catalog that ordering would 403 (`schema '…' does not
> exist`). So deploy.sh creates the 5 operational tables **empty** up front (reusing the idempotent
> `data/genie/01_create_operational_schema.py` via Databricks Connect — no warehouse needed), then
> the seed populates them. This is what keeps the one-shot cold-start working on a brand-new
> catalog/schema. (Skipped on `make redeploy`/`--app-only` — the tables already exist by then.)

> **Already deployed this app on the older Terraform engine?** The bundle now uses the **direct**
> deployment engine (required for `genie_spaces`; GA + default since CLI 1.3.0). Migrate the live
> state **once per target**, rehearsing on `dev` before `demo`:
>
> ```bash
> databricks bundle deployment migrate -t <target> -p <p> --noplancheck   # local-only; undo: rm .databricks/bundle/<target>/resources.json
> databricks bundle plan -t <target> -p <p>                               # GATE: genie space = create, app = UPDATE (never recreate)
> make deploy PROFILE=<p> TARGET=<target>                                 # finalize on direct + create the space
> ```
>
> `--noplancheck` is **required** here: the standard migrate runs its pre-check `plan` on the Terraform
> engine, which rejects the direct-only `genie_spaces` resource (`Genie Space resources are only
> supported with direct deployment mode`). Migrate itself is local-only and just reads existing
> resource IDs into `resources.json` — it adopts resources in place and does **not** recreate the app
> (which would orphan the Lakebase schemas). If the `plan` shows the app being recreated, **stop**. See
> [`lakebase-apps-permissions.md`](lakebase-apps-permissions.md).

### Fast dev loop (after the first full deploy)

Iterating on code? Skip the seed/lakebase steps — just push code and restart the app:

```bash
make redeploy    PROFILE=<p>   # agent-server (Python) change: bundle deploy + bundle run (~30-60s)
make redeploy-ui PROFILE=<p>   # frontend change: npm build + bundle deploy + bundle run
```

These stay on the same target/app and **never delete it**, so the app SP and its Lakebase schemas
persist (only app *delete + recreate* orphans schemas — see
[`lakebase-apps-permissions.md`](lakebase-apps-permissions.md)).

## The data toggle

- **Demo samples (default).** `SEED=true` runs the seed job → operational tables, pgvector, and the
  Knowledge VS index, all in `uc_catalog.uc_schema`. (The Genie space is a DABs resource, created on
  `bundle deploy`, not by the seed.) Works out of the box.
- **Your own data.** Re-point `uc_catalog` / `uc_schema` at your governed tables and deploy with
  `SEED=false`. The app reads whatever the variables point at; nothing is overwritten. (Your
  tables should match the operational schema the agent expects — see `data/genie/genie_config.py`.)

## What used to be manual is now automatic

Two follow-ups that used to be hand-run are folded into `deploy.sh`:

- **App service-principal Lakebase access** — the native `postgres` app resource grants the SP
  `CONNECT` + `CREATE`, the SP self-creates + owns its memory/write-back schemas at startup, and the
  seed's `grant_app_sp` task adds the `SELECT` grant on the synced `public` tables. No manual
  `grant_app_sp.sh` step (that script is gone).
- **Genie space** — now a first-class DABs resource (`resources.genie_spaces`), created and bound to
  the app in a single `bundle deploy` (deploy.sh generates its serialized definition from
  `genie_config.py` first). No more capture-the-id-and-redeploy. `users` gets `CAN_RUN` by default;
  pass `GENIE_GROUP=<group>` to grant a different group instead.

**Two Genie+OBO steps the deploy genuinely can't do** (security-gated — surfaced in the preflight banner):

1. A **workspace admin** enables the **"Databricks Apps – On-Behalf-Of-User Authorization"** Public Preview.
2. **Each end user accepts the OAuth consent** on first open (a stale browser session → 403
   `invalid scope`; re-open in a fresh/incognito session). End users also need `CAN USE` on a
   serverless/pro warehouse and `SELECT` on the underlying tables. (`CAN_RUN` on the space is granted
   to `users` by default — `GENIE_GROUP=<group>` to scope tighter.)

Until those are done the **Analytics/Genie route degrades gracefully** and every other route works.

> **Don't delete the app to "start clean."** Redeploy in place (`make deploy` / `make redeploy`).
> Deleting the app destroys its service principal and orphans the Lakebase schemas it owns. If you
> must recreate, detach the Lakebase resource as `CAN MANAGE` first — see
> [`lakebase-apps-permissions.md`](lakebase-apps-permissions.md).

If you registered the Lakebase database as a UC catalog via CLI and it failed, register it in the
UI (*Catalog Explorer → Create catalog → from a Lakebase database*) — the autoscaling
`create-catalog` CLI path has a known body-stripping bug.

## Troubleshooting (first deploy on a Databricks laptop)

Full table in [`DEPLOYMENT_GUIDE.md` §8](DEPLOYMENT_GUIDE.md#8-troubleshooting). The ones that bite
almost everyone on the first run:

- **`uv` can't reach PyPI** (`Connection refused (os error 61)` / `Failed to fetch https://pypi.org/...`).
  Corp laptops block public PyPI in a **Jamf-managed `/etc/hosts`** — don't edit it (it's reverted +
  it's an IT control). Use the internal proxy: `export UV_INDEX_URL=https://pypi-proxy.cloud.databricks.com/simple`
  in the same shell as `make deploy`.
- **`UC catalog '<name>' not found (or no access)`** — you're deploying to a workspace other than
  the one `databricks.yml`'s committed `uc_catalog` default targets. Preflight fails fast on this
  (rather than burning the ~10min Lakebase-project-provisioning wait first): pass
  `--uc-catalog <name>` (or `--var uc_catalog=<name>`) for a catalog you can write to on *this*
  workspace.
- **`Cluster id or serverless are required but were not specified`** — the local table-creation step
  uses Databricks Connect. Add `serverless_compute_id = auto` to your `[<profile>]` in
  `~/.databrickscfg`. Preflight now warns about this proactively, but a warning isn't a hard stop
  (a `cluster_id` also satisfies it), so it can still surface here.
- **`Genie Space resources are only supported with direct deployment mode`** — the workspace has old
  Terraform-engine state. Migrate once: `databricks bundle deployment migrate -t <target> -p <p>
  --noplancheck` → `databricks bundle plan …` (app must be **UPDATE**, never recreate) → `make deploy`.
  If migrate fails with `missing entry in state` and the app **never deployed**, clean-slate (delete the
  app/job + the remote & local `terraform.tfstate`, then redeploy). Never clean-slate an app that has run.
- **`lineage mismatch in state files`** — you're reusing a target name (e.g. `dev`) across two
  workspaces, or deploying from a stale/duplicate copy of the repo. Run from the bundle root and
  `rm -rf .databricks/bundle/<target>` to re-pull the right remote state.
- **End-of-deploy `⚠ Degraded … seed`/`verify` warnings are usually cosmetic** — the seed-job poll can
  time out while the job runs on (check the Run URL), and `verify_deploy.py` probes the *default*
  `lakebase_project` name so it "fails" when you passed `--var lakebase_project=…` even though the app
  is bound correctly. The core app is up either way.

## Verify

```bash
databricks apps list --profile <p>        # supply-chain-planner → RUNNING, note the URL
```

Open the app URL `/ui`, ask *"Have we seen a disruption like the PrecisionBond recall before?"*,
and confirm a traced answer. Traces land in the bundle's experiment
(`/Users/<you>/supply_chain_planner-<target>-traces`); 👍/👎 attach as assessments.

> **Validate a whole *fresh* deploy automatically** (deploy → seed → verify → teardown, all against
> throwaway resources, safe to run anytime): `make integration-test PROFILE=<p> CATALOG=<catalog>`.
> See [`test/integration-testing.md`](test/integration-testing.md).

## Tear down

```bash
make destroy PROFILE=<p>      # removes the App, experiment, and job (NOT the Lakebase project or your catalog)
```
