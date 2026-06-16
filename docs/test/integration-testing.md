# Integration Testing — Supply-Chain Planner Copilot

How to set up the seeded data and verify the stack end-to-end, running **locally (authed to
Databricks)** or **on Databricks compute**. The keystone test is
[`data/operational/04_verify_hybrid_query.py`](../../data/operational/04_verify_hybrid_query.py):
it exercises the whole operational path in one run — Lakebase auth → pgvector index → embedding
endpoint → the synced relational tables → the hybrid join.

> Two layers: the **cold-start E2E** (next section) validates a whole fresh deploy in one command;
> the **tiers** below are granular local/stack checks (data, the hybrid query, Genie). The operational
> hybrid query is wired into the agent (`operational_tool.py`); see [§6](#6-agent-e2e-full-graph).

## Cold-start E2E — validate a fresh deploy (one command)

The fastest way to prove a **fresh-workspace** deploy actually works — genie table-ordering, the
BYO-warehouse path, the seed (incl. pgvector), and the Lakebase permission contract — is the
cold-start harness. It deploys to a **throwaway** Lakebase project + UC schema in an isolated git
worktree, runs `verify_deploy`, then tears everything down. Teardown is always safe: the app service
principal only ever owns schemas inside a project that's deleted wholesale (no orphaned-schema risk —
see [`../lakebase-apps-permissions.md`](../lakebase-apps-permissions.md)).

```bash
make integration-test PROFILE=<p> CATALOG=<existing-writable-catalog>
# restricted workspace (deployer can't create a SQL warehouse):
make integration-test PROFILE=<p> CATALOG=main ITEST_ARGS="--target byo --sql-warehouse-id <id>"
```

- Tests **committed** code (HEAD) — commit first. Prereqs: same as `make deploy` plus
  `serverless_compute_id=auto` in the profile (the harness defaults it) and CREATE on `CATALOG`.
- **Green run** = deploy + seed + `verify_deploy` all pass, then clean teardown (no leftovers).
- Full flags + guardrails: the **`integration-test` skill** or `scripts/integration_test.sh --help`.
- On a borrowed catalog you don't own (e.g. `main`), MLflow UC-tracing degrades to artifact storage
  (the grant needs `MANAGE` on the trace catalog) — non-fatal; every route still works.

## Test tiers

| Tier | What | Needs Databricks? | Where |
|---|---|---|---|
| **0 — Local data checks** | `seeds.py` determinism, FK integrity, incident reachability, `eval_set.py` literals | No (pure Python) | local |
| **1 — Data setup** | Build the Delta tables, pgvector table, and Synced Tables | Yes | local (DB Connect) or Databricks |
| **2 — Operational integration** | `04_verify_hybrid_query.py` — hero scenario + access scoping | Yes (Lakebase + embeddings) | local or Databricks |
| **3 — Genie integration** | Ask the certified `eval_set.py` questions, assert the literals | Yes (Genie space + warehouse) | local or Databricks |
| **4 — Knowledge (P1)** | PDFs → chunks → Vector Search | Yes | local + Databricks |
| **5 — Agent E2E** | Full graph run with HITL + MLflow trace | Yes | eval flywheel / manual /ui (§6) |

Run them in order; each tier assumes the previous passed.

---

## Tier 0 — Local data checks (no Databricks)

Fast feedback with zero infra — `seeds.py` and `eval_set.py` are pure Python. Run from the repo root:

```bash
# Determinism + FK + hero math + incident reachability through the hybrid INNER JOIN
python3 -c "
from data.operational import seeds as s
op={(r['supplier_id'],r['sku']) for r in s.build_open_pos()}
inv={r['sku'] for r in s.build_inventory_current()}
qi=s.build_quality_incidents()
reach=lambda r:(r['supplier_id'],r['sku']) in op and r['sku'] in inv and not r['expired']
prod_cat={p['sku']:p['category'] for p in s.build_products()}
assert all(r['supplier_id'] in s.suppliers_by_category(prod_cat[r['sku']]) for r in s.build_purchase_orders()), 'FK violation'
assert {r['sku']:r['on_hand_qty'] for r in s.build_inventory_current()}[s.HERO_SKU]==40.0, 'hero on-hand'
assert sum(reach(r) for r in qi)==sum(not r['expired'] for r in qi), 'some active incident unreachable'
print('Tier 0 OK: FK clean, hero on-hand=40, all active incidents reachable')
"
# Certified Genie expectations (what Tier 3 will assert)
python3 -m data.operational.eval_set
```

**Pass:** prints `Tier 0 OK`; `eval_set` shows SKU-1001 on-hand=40, open-PO sum=800, gap=760, Henkel
top at-risk. These come straight from `seeds.py`, so a green Tier 0 means the data the demo relies on
is internally consistent before you touch any compute.

> Recommended next step: promote these into `pytest` tests so they run in CI (no workspace needed).

---

## Prerequisites (one-time)

| Need | How |
|---|---|
| CLI auth | `databricks auth login --host https://<ws>.cloud.databricks.com --profile <name>` |
| `.env` | `cp .env.example .env`; set `DATABRICKS_CONFIG_PROFILE`, `UC_CATALOG`/`UC_SCHEMA`, `DATABRICKS_WAREHOUSE_ID` |
| Lakebase project + branch | Create in the workspace UI (autoscaling); set `LAKEBASE_AUTOSCALING_PROJECT`, `LAKEBASE_AUTOSCALING_BRANCH`, `LAKEBASE_AUTOSCALING_ENDPOINT` (bare endpoint id is fine — it's combined into `projects/<p>/branches/<b>/endpoints/<id>`) |
| Register Lakebase as a UC catalog | `databricks postgres create-catalog <name> ...` → set `LAKEBASE_UC_CATALOG=<name>` (needed by Synced Tables in step 3) |
| Embedding endpoint | `databricks-gte-large-en` (system endpoint; normally already available) |
| Local Spark | `uv sync` installs `databricks-connect` (dev-only). For serverless, set `serverless_compute_id = auto` in your CLI profile |
| Sanity | `uv run python -c "from databricks.sdk import WorkspaceClient; print(WorkspaceClient().current_user.me().user_name)"` |

One autoscaling config (`PROJECT` + `BRANCH` + `ENDPOINT`) drives the whole 01→04 run.
`LAKEBASE_OPERATIONAL_SCHEMA` (default `public`) is the Postgres schema holding both the synced
relational tables and the native `quality_incidents` pgvector table.

---

## Tier 1 — Data setup (run in order)

All steps run **locally** (Spark steps via Databricks Connect through `get_spark()`) **or on
Databricks** (notebook/job, ambient session) — same code either way.

| # | Command | What / pass signal |
|---|---|---|
| 1 | `uv run python data/genie/01_create_operational_schema.py` | Empty 5-table DDL + column comments (idempotent). |
| 2 | `uv run python data/operational/01_generate_genie_tables.py` | Populates the 5 Genie tables + `inventory_current`/`open_pos`. Prints **hero on-hand = 40.0**, **PO FK orphans = 0**. |
| 3 | `uv run python data/operational/02_pre_seed_pgvector.py` | Creates the native `quality_incidents` pgvector table, embeds descriptions, builds the HNSW index. Smoke print: top-5 for the hero query are Henkel/SKU-1001 cracking rows. |
| 4 | `uv run python data/operational/03_sync_to_lakebase.py` | Creates 6 Synced Tables; **then grant the App SP `SELECT`** (it prints the GRANTs). Watch `databricks postgres get-synced-table …` until each is `SUCCEEDED`. |
| 5 | `uv run python data/genie/build_geniespace_json.py` | Generates `supply_chain.geniespace.json` (the `genie_spaces` DABs resource ships it on deploy). Local SDK alternative: `02_create_genie_space.py` → paste the printed `GENIE_SPACE_ID` into `.env`. |

> Step 1 is mainly there to establish the column-comment contract; `operational/01` overwrites the
> same tables. **Reseeding after step 3:** a full overwrite is non-incremental, so re-running step 2
> after a CONTINUOUS synced table is live forces a re-snapshot — refresh/recreate the sync after any reseed.

### Step 4 prerequisite — register the Lakebase UC catalog (one-time, needs `CREATE CATALOG`)

Synced tables target a UC catalog that maps to the Lakebase Postgres database. That catalog is
**not** a regular storage catalog — register it once per project with `databricks postgres
create-catalog`. **This requires `CREATE CATALOG` on the metastore** (UC checks the grant even when
the catalog already exists, so `IF NOT EXISTS`-style retries won't help a non-admin). If you hit
`PERMISSION_DENIED: User does not have CREATE CATALOG on Metastore`, ask a metastore admin to grant
it (or to run the command for you).

```bash
# branch must match LAKEBASE_AUTOSCALING_BRANCH; database is LAKEBASE_DATABASE (default databricks_postgres)
databricks postgres create-catalog <LAKEBASE_UC_CATALOG> \
  --json '{"spec":{"postgres_database":"databricks_postgres",
                   "branch":"projects/<LAKEBASE_AUTOSCALING_PROJECT>/branches/<LAKEBASE_AUTOSCALING_BRANCH>"}}' \
  --profile <profile>
```

Then set `LAKEBASE_UC_CATALOG=<name>` in `.env` and run step 4, then Tier 2:

```bash
uv run python data/operational/03_sync_to_lakebase.py            # creates the 6 Synced Tables + prints GRANTs
# watch each until SUCCEEDED:
databricks postgres get-synced-table synced_tables/<LAKEBASE_UC_CATALOG>.public.inventory_current --profile <profile>
uv run python data/operational/04_verify_hybrid_query.py         # Tier 2 keystone — must print ✓ All hybrid-query assertions passed
```

The synced relational tables and the native `quality_incidents` pgvector table (step 3) both land
in the same Postgres `public` schema, so once step 4 succeeds the Tier 2 hybrid query can join them
in one statement.

---

## Tier 2 — Operational integration (the keystone)

```bash
uv run python data/operational/04_verify_hybrid_query.py
```

**Pass** = exit 0, `✓ All hybrid-query assertions passed`:

- **Hero adhesive-cracking query:** top-5 are Henkel/SKU-1001 adhesive-cracking incidents; top row
  `on_hand = 40`, `open_po = 500`; the `SUPERSEDED` (expired) row is absent; cluster A dominates
  (≥4/5). The query runs as the app service principal — identity-independent, so every user gets
  these same rows.

This single run validates: Lakebase OAuth connection, `CREATE EXTENSION vector` + HNSW index, the
embedding endpoint, the synced relational tables, and the similarity-as-a-predicate join.
A non-zero exit prints exactly which assertion failed.

---

## Tier 3 — Genie analytics integration

In the Genie space (UI) or via the Conversation API, ask the certified questions from
[`eval_set.py`](../../data/operational/eval_set.py) and check the literals (which Tier 0 printed):

| Question | Expected |
|---|---|
| Current on-hand for SKU-1001 | **40** |
| Total open PO qty for SKU-1001 | **800** |
| Coverage gap for SKU-1001 | **760** |
| At-risk suppliers, ranked | **Henkel AG (82)** top |
| Suppliers that can supply adhesives | Henkel, DuPont, BASF, Dow, 3M |

Because the expected values are **derived from `seeds.py`**, they can't drift from the data. The
MLflow `mlflow.genai` harness that automates this (deterministic answer-match + LLM faithfulness
judge) is **P2** — author the dataset from `eval_set.py` now, wire the harness later.

---

## Tier 4 — Knowledge corpus (optional, P1)

Independent of the operational test. PDFs are vendored at `data/knowledge/bronze_documents/`.

```bash
uv run python data/knowledge/01_upload_pdfs.py      # local — uploads to the UC volume
# then on Databricks:
#   data/knowledge/02_parse_and_chunk.py             — PDFs → knowledge_chunks Delta (CDF on)
#   data/knowledge/03_build_vs_index.py              — Vector Search Delta Sync index
```

**Pass:** the index reaches an online state and a smoke `similarity_search` returns relevant passages.
Paste the printed `VECTOR_SEARCH_ENDPOINT` / `VECTOR_SEARCH_INDEX` into `.env`.

---

## 6. Agent E2E (full graph)

`agent_server/tools/operational_tool.py` now runs the resolved hybrid query against seeded Lakebase
data, so a full supervisor → gather → planner → gate → HITL → commit run hits real rows. What isn't an
automated test *here* yet is the whole-graph run itself — covered today by the **eval flywheel**
(`agent_server/evaluation/`, the `flywheel` skill) and a manual `/ui` smoke. A dedicated Tier 5 would
assert: the canonical question routes correctly, Operational + Genie return real rows, the planner
trips the gate, the HITL `interrupt()` pauses and resumes from the Lakebase checkpoint, and the whole
run is one coherent MLflow trace.

---

## Recommended split & troubleshooting

**Split:** Tier 0 anywhere; Spark steps (1–2) via local Databricks Connect *or* a Databricks notebook;
psycopg/CLI steps (3–5) and the Tier 2 verify locally.

| Symptom | Likely cause / fix |
|---|---|
| `No Lakebase connection configured` | Set `LAKEBASE_AUTOSCALING_ENDPOINT` (+ project/branch) or `LAKEBASE_INSTANCE_NAME` in `.env` |
| Step 1/2 fails locally with a Spark/session error | `databricks-connect` not installed (`uv sync`) or no serverless compute — set `serverless_compute_id = auto` in the profile |
| Step 3 synced table fails | `LAKEBASE_UC_CATALOG` not registered (`databricks postgres create-catalog`), or missing `USE_SCHEMA`/`CREATE_TABLE` grants |
| Tier 2 query returns 0 rows | The operational tables aren't seeded/synced — re-run steps 2–3; check the pgvector `quality_incidents` count in the Explorer drawer |
| Embedding count mismatch (step 2) | Embedding endpoint unreachable or returned partial — check the endpoint name in `.env` |

See [`data/operational/README.md`](../../data/operational/README.md) for the data design and the
production access-control options.
