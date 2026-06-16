# Plan — genie_spaces cold-start fix (Option A) + integration-test skill

> Persisted hand-off for a fresh session. Branch: `feat/genie-spaces-dabs-resource` @ `5d4d4f9`
> (pushed; PR not opened). Untracked scratch doc — delete or fold into the PR when done.

## Why this exists (findings from the e2-demo-west cold-start test, 2026-06-15)

Testing the one-shot deploy against a **fresh workspace** (e2-demo-west, `uc_catalog=main`) surfaced
**two cold-start blockers** that the `dev` deploy never hit (dev was already seeded):

1. **Genie-space table-ordering bug (the important one).** `bundle deploy` creates the
   `resources.genie_spaces` resource, and the Genie create API **validates that its referenced tables
   exist** (`<uc_catalog>.<uc_schema>.{suppliers,product_dim,inventory,purchase_orders,supplier_status}`).
   But those tables are created by the **seed job, which runs AFTER `bundle deploy`**. So on any fresh
   catalog/schema the deploy fails:
   `Error: cannot create resources.genie_spaces.supply_chain_analytics: Schema 'main.planner.inventory' does not exist … (403)`.
   `dev` only worked because the tables pre-existed. **This breaks the one-shot `make deploy` cold-start.**

2. **BYO-warehouse gap (Tier-2, separate).** The bundle always creates `sql_warehouses.app_sql_warehouse`.
   In a workspace where the deployer lacks SQL-warehouse-create entitlement (e2-demo-west), `bundle deploy`
   403s. Confirmed workaround in the test: remove the warehouse resource + pin `sql_warehouse_id` to an
   existing warehouse id. (`--var sql_warehouse_id=<id>` alone is NOT enough — the resource is still declared.)

## ✅ IMPLEMENTED (2026-06-15, this branch) — A1, not A2

Chose **A1 (Databricks Connect)** over the originally-recommended A2 (SQL Statement Execution API)
because of a **warehouse chicken-and-egg A2 can't escape**: A2 needs a warehouse to run the DDL, but
on the default path the warehouse is *bundle-created* and doesn't exist until `bundle deploy` — i.e.
*after* the pre-deploy step that needs it. A1 needs **no warehouse** (Databricks Connect serverless),
reuses the existing idempotent `data/genie/01_create_operational_schema.py` verbatim, and so composes
cleanly. Its only prereq (`serverless_compute_id=auto` in the profile) is one the project already
assumes (the seed runs on serverless).

What landed:
- `scripts/deploy.sh`: new `create_operational_tables()` phase, run **after `build_geniespace`, before
  `bundle_deploy`** in the full path only (skipped on `--app-only` — tables already exist). New
  first-class flags `--uc-catalog` / `--uc-schema` / `--sql-warehouse-id` (sugar over `--var`). A
  `byo`-target guard that fails fast if no warehouse id is supplied.
- `databricks.yml`: warehouse moved out of top-level `resources:` into **per-target** `resources:`
  (`dev` declares it with a YAML anchor, `demo` aliases it); `var.sql_warehouse_id` set per-target
  (no top-level default); new **`byo` target** omits the warehouse for restricted workspaces.
- Docs: `docs/DEPLOY.md` (phases + BYO section) + `docs/DEPLOYMENT_GUIDE.md` (variables table +
  entitlements) updated. Validated: `bundle validate` passes for `dev`/`demo`/`byo`(+id) and fails
  cleanly for `byo` without an id.

Still TODO: re-run the cold-start E2E in a full-perms workspace (item 4) + the integration-test skill
(item 3). The fix is offline-validated only.

## Option A — create the empty operational tables BEFORE `bundle deploy` (primary fix)

Make the 5 operational tables EXIST (empty) before the genie space is applied, so the create-API
validation passes; the seed populates them later. Keeps genie-as-DABs-resource + one-shot.

- Add a `create_operational_tables` step in `scripts/deploy.sh`, run **after `build_geniespace`,
  before `bundle_deploy`**, that creates `<uc_schema>` + the 5 empty tables in `<uc_catalog>`.
- The DDL already exists in `data/genie/01_create_operational_schema.py` (idempotent, empty tables +
  column comments). Two ways to run it pre-deploy:
  - **A1 (reuse existing script, needs Spark):** `uv run python data/genie/01_create_operational_schema.py`
    with `UC_CATALOG`/`UC_SCHEMA` env. Uses `get_spark()` → requires Databricks Connect locally
    (profile `serverless_compute_id=auto`). Adds a Spark dep to the deploy front-end.
  - **A2 (SQL Statement Execution API, no Spark) — RECOMMENDED:** run the `CREATE SCHEMA` + `CREATE TABLE
    IF NOT EXISTS` statements via `/api/2.0/sql/statements` against the warehouse (BYO or created). No
    Spark dep (aligns with "agent app never uses Spark"). Needs the DDL as portable SQL — extract from
    `01_create_operational_schema.py` (its `spark.sql("CREATE TABLE …")` strings are portable) into e.g.
    `data/genie/operational_ddl.sql`, or generate it from a shared schema definition so it can't drift.
- Edge cases / checks:
  - Deployer needs `CREATE SCHEMA`/`CREATE TABLE` on `uc_catalog` (separate from warehouse-create).
  - Tables must be visible to the deployer (Genie create runs as the deployer) — CREATE makes them owned → visible.
  - `--app-only` path: tables already exist on redeploy, so the IF-NOT-EXISTS DDL is a cheap no-op; decide
    whether to run it there too (safe either way).
  - Update deploy.sh phase comments + `docs/DEPLOY.md` / `DEPLOYMENT_GUIDE.md` (genie space now depends on a
    pre-deploy table-DDL step).
- **Re-test cold-start** in a workspace where you have full rights (CREATE on the catalog, warehouse use).

## BYO-warehouse (Tier-2, optional but recommended for restricted workspaces)

Make `app_sql_warehouse` optional so the bundle deploys without warehouse-create entitlement:
- DABs has no native conditional resource. Cleanest options: (a) a dedicated target (e.g. `byo`) that omits
  the warehouse resource and sets `sql_warehouse_id` to an existing id; or (b) move the warehouse into a
  per-target `resources:` block so only warehouse-capable targets create it.
- Validated in the e2-demo-west test: removing the resource + pinning `sql_warehouse_id=<existing>` works.

## Integration-test skill — git-worktree cold-start E2E

Build a **skill** (preferred over a bare slash command, for the safety guardrails) that runs the full
cold-start E2E in an isolated worktree and tears it down:
- Inputs: `profile`, `uc_catalog`, `lakebase_project` (+ optional `target`, BYO warehouse id, `seed`).
- Steps it automates:
  1. `git worktree add <tmp> --detach <branch>` → clean `.databricks/` state (no cross-workspace ID mixing).
  2. `uv sync --directory <tmp>`.
  3. (optional) apply BYO-warehouse var/edit.
  4. `make -C <tmp> deploy PROFILE=<p> VARS="uc_catalog=<c> lakebase_project=<proj> …"`.
  5. Verify: app ACTIVE + `verify_deploy` passes + genie space created (`w.genie.list_spaces`); optional /ui smoke.
  6. Teardown: `bundle destroy --auto-approve` + delete the Lakebase project (`w.postgres.delete_project`) +
     `git worktree remove --force`.
- **Teardown safety (critical):** only `bundle destroy` when no app SP owns durable Lakebase schemas, OR —
  better — have the skill create a **per-run throwaway Lakebase project** (`lakebase_project=scp-itest-<ts>`)
  so the app SP only ever owns throwaway schemas → teardown is always safe (sidesteps the app-delete /
  schema-orphan rule in CLAUDE.md / `docs/lakebase-apps-permissions.md`).
- References: `docs/test/integration-testing.md`, `scripts/deploy.sh`, `scripts/verify_deploy.py`.

## State at hand-off
- Branch `feat/genie-spaces-dabs-resource` @ `5d4d4f9` pushed; PR NOT opened.
- `dev` (profile `mfg-sc-agent`) migrated Terraform→direct + genie space live (worked: pre-seeded).
- e2-demo-west test leftovers fully torn down (bundle destroyed, Lakebase project deleted, worktree removed).
- Cold-start ordering bug + BYO-warehouse gap are **NOT yet fixed** in the branch.

## Next session: ordered to-do
1. ~~Implement **Option A** (decide A1 vs A2). Update deploy.sh + docs.~~ ✅ DONE — A1 (see above).
2. ~~(Optional) BYO-warehouse target.~~ ✅ DONE — `byo` target.
3. ~~Build the **integration-test skill** (per-run throwaway Lakebase for safe teardown).~~ ✅ DONE:
   - `scripts/integration_test.sh` — worktree (tests committed HEAD) → uv sync → `make deploy` with a
     throwaway Lakebase project **and** throwaway UC schema → verify (app ACTIVE + `verify_deploy` +
     Genie `list_spaces`) → EXIT-trap teardown. Added throwaway-**schema** beyond the plan so each run
     actually exercises the cold-start (tables don't pre-exist); teardown drops it `CASCADE`.
   - `scripts/itest_teardown.py` — drops the throwaway schema + deletes the throwaway project; refuses
     any name lacking the `itest` marker (unless `--force`).
   - `.claude/skills/integration-test/SKILL.md` (+ README entry) and a `make integration-test` target.
   - Offline-validated only: bash-3.2 syntax, `--help`, the teardown safety guard. **No live run yet.**
4. Re-run the cold-start E2E in a full-perms workspace via the skill (validates A1 + byo + the harness
   for real — everything above is offline-validated only).
5. Open the PR (note: cold-start fix, BYO-warehouse, integration test).
