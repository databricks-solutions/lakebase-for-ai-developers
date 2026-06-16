---
name: integration-test
description: "Run the full cold-start end-to-end deploy test in an isolated git worktree with always-safe teardown. Use when: (1) verifying the one-shot `make deploy` works on a FRESH workspace/catalog (cold-start), (2) the user says 'integration test', 'cold-start test', 'test the deploy end to end', or 'does deploy still work on a new workspace', (3) validating a change to deploy.sh / databricks.yml / the seed before opening a PR. PROJECT-AUTHORED (not a vendored skill)."
---

# Cold-start integration test (git-worktree E2E)

Drives [`scripts/integration_test.sh`](../../../scripts/integration_test.sh): deploy the whole stack
to a **brand-new** Lakebase project + UC schema in an isolated worktree, verify it, then tear it all
down. This is the real proof that `make deploy` is genuinely one-shot on a fresh workspace — in
particular that the `genie_spaces` table-validation ordering fix holds (empty operational tables are
created before `bundle deploy`).

## When to use it

- After changing `scripts/deploy.sh`, `databricks.yml`, or the seed — before opening a PR.
- To regression-test the cold-start path (genie table-ordering, BYO-warehouse, pgvector) — see the
  cold-start E2E section in [`docs/test/integration-testing.md`](../../../docs/test/integration-testing.md).
- To validate a fresh-workspace deploy without hand-clicking through the UI.

This is **not** a fast check — it provisions a real Lakebase project + app (~10 min) and then deletes
them. Confirm with the user before running.

## Safety model (why teardown is always safe here)

The harness only ever creates **throwaway** resources, each tagged with the marker `itest`:

- A throwaway **Lakebase project** (`scp-itest-<epoch>`). The app service principal owns its
  memory/write-back schemas, and `bundle destroy` (app delete) **orphans** those schemas —
  unrecoverable in a shared project (see
  [`docs/lakebase-apps-permissions.md`](../../../docs/lakebase-apps-permissions.md)). Because the
  project is throwaway, teardown just deletes the whole project, orphans and all.
- A throwaway **UC schema** (`scp_itest_<epoch>`) inside an existing catalog — so the run actually
  exercises the cold-start (tables don't pre-exist), and teardown drops it `CASCADE`.

`itest_teardown.py` **refuses** to delete any project/schema whose name lacks the `itest` marker
(unless `--force`), so the harness can't nuke a real project like `mfg-supply-chain-copilot` or the
`planner` schema. Teardown runs from an `EXIT` trap, so it fires even if the deploy fails partway.

**Never** point `--lakebase-project` / `--uc-schema` at a real (shared) project or schema — let the
harness auto-generate the throwaway names. The catalog (`--uc-catalog`) is **reused, never deleted**;
pass an existing writable one (e.g. `main`).

## Inputs to gather from the user

Required: a **CLI profile** (`--profile`) and an **existing, writable UC catalog** (`--uc-catalog`).
Ask which workspace/profile, and confirm the catalog. Then check:

- **Committed?** The worktree tests **committed** code (`HEAD` by default). If there are uncommitted
  changes to `deploy.sh` / `databricks.yml` / the seed, tell the user to commit them first (or note
  they won't be tested). Pass a different ref with `--ref <branch|sha>`.
- **Can the profile create a SQL warehouse?** If not (restricted workspace), run with
  `--target byo --sql-warehouse-id <existing-id>`.
- **Seed?** Default seeds demo data. `--no-seed` is faster and still proves the deploy + cold-start.

Prereq the user must already have: `serverless_compute_id=auto` in the profile (the cold-start table
DDL and the schema-drop teardown use Databricks Connect). Same other prereqs as `make deploy`.

## Run it

```bash
# Standard (auto-create warehouse):
scripts/integration_test.sh --profile <p> --uc-catalog main

# Restricted workspace (bring your own warehouse):
scripts/integration_test.sh --profile <p> --uc-catalog main --target byo --sql-warehouse-id <id>

# Debug a failure — leave everything up (NO teardown), then clean up by hand using the printed commands:
scripts/integration_test.sh --profile <p> --uc-catalog main --keep
```

## Interpreting the result

- **Core assertion:** `make deploy` exits 0. A green deploy on a fresh project + schema **is** the
  cold-start fix working (it ran the pre-deploy table DDL, then created the Genie space without the
  "schema does not exist" 403). If this step dies, the cold-start path is broken — read the deploy
  output; the harness still tears down.
- **Health checks (reported, non-core):** app compute `ACTIVE`, Genie space present via
  `list_spaces`, and `verify_deploy.py` (Lakebase permission contract). A failed health check exits
  non-zero but still tears down — investigate the `⚠` lines.
- The script prints **PASS** or the failing checks, then runs teardown (unless `--keep`).

## If something goes wrong

- **Stuck / interrupted before teardown:** re-run the teardown by hand —
  `uv run python scripts/itest_teardown.py --project scp-itest-<ts> --catalog <cat> --schema scp_itest_<ts>`
  then `git worktree list` / `git worktree remove --force <path>`.
- **Don't retry blindly** more than once or twice — if the deploy keeps failing the same way, surface
  the deploy output to the user; it's the same code path as `make deploy`, so the fix belongs in
  `deploy.sh` / `databricks.yml`, not the harness.

## References

- [`scripts/integration_test.sh`](../../../scripts/integration_test.sh) — the orchestrator (flags + phases).
- [`scripts/itest_teardown.py`](../../../scripts/itest_teardown.py) — guarded throwaway teardown.
- [`docs/test/integration-testing.md`](../../../docs/test/integration-testing.md) — the tiered data/stack tests this complements.
- [`docs/DEPLOY.md`](../../../docs/DEPLOY.md) — the deploy phases this exercises end to end.
