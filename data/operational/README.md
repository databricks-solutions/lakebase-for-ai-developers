# data/operational/ — operational dataset + the hybrid similarity-join query

Synthetic **structured** data for the Supply-Chain Planner Copilot, plus the operational-Lakebase
layer that backs the canonical demo:

> *"Show me similar quality issues for this supplier, joined to on-hand inventory and open POs."*

Everything is deterministic ([`seeds.py`](seeds.py): fixed RNG seed + hand-set hero rows), so the
demo and the Genie evals never drift. Same files run in the IDE and on Databricks (auth via the
centralized env-aware `agent_server.config.settings`).

## The narrative

**Apex Industrial** buys raw materials from upstream suppliers (the real supplier names from the
`strategic_revenue_demo` knowledge corpus — Henkel, DuPont, Nucor, Saint-Gobain, … — so the
Knowledge / Genie / Operational agents describe the same world), produces goods across 5 categories
(adhesives, fasteners, abrasives, safety, tools), and sells to OEMs. The planner decides what to
expedite, re-source, or hold.

**Hero scenario:** Henkel (`SUP-001`, adhesives) is `at_risk` and has a recurring cluster of
adhesive-cracking quality issues on `SKU-1001` (Structural Epoxy Adhesive). On-hand is only **40**;
the one same-source replacement PO (Henkel, 500) is risky, and there's an alternate-source PO
(DuPont, 300). That recurrence + the coverage gap is what trips the planner's gate.

## Two paths into Lakebase (the key mechanic)

| Path | Tables | Why |
|---|---|---|
| **Synced Tables** (managed Delta→Postgres mirror, read-only) | `inventory_current`, `open_pos` (`LAKEBASE_SYNC_MODE`, default Snapshot; CDF on so it can flip to Continuous); `suppliers`, `product_dim`, `supplier_status` (Snapshot) | Relational data, no vectors — let the managed sync keep it fresh |
| **Native pre-seeded pgvector table** (written directly via psycopg) | `quality_incidents` (`embedding vector(1024)`) | A Delta `array<float>` syncs to Postgres `jsonb` (not a real `vector`), and synced tables are read-only — so the vector table can't ride a sync. We `CREATE TABLE` + compute embeddings via the endpoint + `INSERT ::vector` + `CREATE INDEX hnsw` ourselves |

Lakebase pgvector has **no managed-embeddings option** (unlike Vector Search) — you always compute
and insert. This is why `02` calls the embedding endpoint. (We do **not** hand-generate embeddings
for the long-term memory store — that's `AsyncDatabricksStore`, which embeds on write and is kept
lean/durable, never loaded with operational facts.)

## Tables

**Genie (Analytics agent), in `{catalog}.{schema}`** — `suppliers`, `product_dim`, `inventory`,
`purchase_orders`, `supplier_status` (schema = `data/genie/01_create_operational_schema.py`).

**Operational helpers (gold; synced to Lakebase)** — `inventory_current(sku, on_hand_qty)`,
`open_pos(supplier_id, sku, open_po_qty, next_expected_date)`.

**Operational pgvector (native Lakebase)** — `quality_incidents(incident_id, supplier_id, sku,
category, summary, description, severity, status, incident_date, expired_at, embedding vector(1024))`.
`summary` → `OperationalRow.summary`; `category` groups the semantic clusters (never surfaced in the
row shape); `expired_at IS NULL` = active.

## The resolved hybrid query

```sql
SELECT m.summary, m.supplier_id, m.sku, i.on_hand_qty, po.open_po_qty,
       1 - (m.embedding <=> :q) AS similarity
FROM quality_incidents m
JOIN inventory_current i ON m.sku = i.sku
JOIN open_pos          po ON m.supplier_id = po.supplier_id AND m.sku = po.sku
WHERE m.expired_at IS NULL
ORDER BY m.embedding <=> :q
LIMIT 5;
```

This replaces the stub in `agent_server/tools/operational_tool.py`; the agent returns the SQL in
`OperationalResult.sql` for traceability. `04_verify_hybrid_query.py` asserts it reproduces the hero
result (cluster A dominates the top-5).

## Access governance — SP-governed today; per-user scoping is a documented production option

Operational reads run as the **app service principal** (the Lakebase pool uses ambient SP creds),
so every authenticated app user sees the same UC-governed data. There is **no per-user row scoping**.

A `user_access` ACL + an in-query `JOIN user_access ON ua.user_id = :user_id` predicate used to scope
results to a demo "in-scope planner." It was removed: the ACL was seeded only for whoever ran the
seed job (plus a fake out-of-scope user), so **every other user — every FE and customer — silently
got zero rows**. That trap isn't worth a demo-only feature.

**If per-user product-code scoping is needed in production**, add it without an app-side ACL table:

- **Postgres-native RLS** (FGAC Design Guide "Path C"). UC row filters/column masks do **not**
  propagate to synced tables, so enforcement lives in Postgres. With per-user/OBO DB connections the
  Lakebase Data API maps the Databricks OAuth identity to a Postgres role, so `current_user()` is the
  caller's email. An `ENABLE ROW LEVEL SECURITY` + `CREATE POLICY ... USING (...)` on the operational
  tables then enforces scope on **every** query path (including the similarity search), can't be
  bypassed, and is auditable.
- **Or** an entitlements join driven by a real identity source (a governed entitlements table kept in
  sync with the customer's IdP), not a hand-seeded demo ACL.

(Genie's analytics tables are governed by Unity Catalog out of the box via OBO — no extra work there.)

## Agent memory: dev vs prod (isolate by Lakebase BRANCH)

The LangGraph **checkpointer + store** live in their own schema (`LAKEBASE_AGENT_MEMORY_SCHEMA`),
distinct from the operational `public` schema above.

**Why branches, not separate schemas or projects.** dev + demo are the **same workspace**, so the
idiomatic Lakebase isolation is **copy-on-write branches within the one project** (the internal
Enterprise Lakebase Design Guide's "Simple Dev/Prod" topology: a `production` branch + a
`development` branch). Branches isolate **data, role state, and GRANTs** and get their own endpoint
— schemas alone isolate none of that. Separate *projects* are reserved for **cross-workspace /
multi-tenant / regulatory** isolation (branches-across-workspaces is an explicit anti-pattern). We
therefore use the **same schema name in every environment** — the branch is the boundary.

| Environment | Branch | Schema | Connects via |
|---|---|---|---|
| Local dev | `development` (clone of `production`) | `supply_chain_planner_memory` | `.env` (`LAKEBASE_AUTOSCALING_BRANCH=development`) |
| Deployed App (demo) | `production` | `supply_chain_planner_memory` | `databricks.yml` (`var.lakebase_branch[_name]`, demo target) |

Create the dev branch once (copy-on-write; instant; pay only for changed pages):

```bash
databricks postgres create-branch projects/mfg-supply-chain-copilot development \
  --json '{"spec":{"source_branch":"projects/mfg-supply-chain-copilot/branches/production","no_expiry":true}}' \
  --profile mfg-sc-agent
```

**Caveat — synced tables don't follow a branch.** A child branch **clones the synced-table data**
(a frozen snapshot via copy-on-write) but the **sync pipeline keeps targeting the original branch**,
and "synced tables + branch reset" is **not GA-hardened**. For this repo that's a non-issue: the
operational data is **static/seeded**, so the dev branch's snapshot never drifts. If you ever need
live operational updates on a branch, create a separate synced table targeting that branch. Keep the
production synced tables pinned to `production`.

**Ownership + deploy-first (per branch).** Whoever runs `store.setup()` / `checkpointer.setup()`
FIRST owns the memory schema **on that branch**, and a principal with only `CAN_CONNECT_AND_CREATE`
(the App SP) **cannot write into a schema owned by someone else** — and Postgres ownership **can't
be reassigned**. So **deploy the App before its first run on a branch** so the SP owns the schema
there; run locally afterward with your own OAuth identity. (Running locally first is exactly how a
split `…_memory` (you) + `…_memory_app` (SP) arose on `production`.) Note: the **app→DB binding grant
needs a workspace admin** — `CAN_MANAGE` on the instance is not enough (this bit the mfg + credit
DAIS booth apps with *"User does not have permission to grant permissions for added resource:
database"*).

**Inspect another principal's memory schema** (read-only, no ownership transfer) — run as a schema
owner / `databricks_superuser`:

```bash
uv run python scripts/grant_lakebase_permissions.py <your-email> \
    --schema supply_chain_planner_memory --mode read
```

**One-time cleanup if `production`'s memory schema is mis-owned** (your user owns it, so the App SP
can't write and falls back to `…_memory_app`): this is a **destructive prod action — get explicit
go-ahead and export first**. Either (A) `DROP SCHEMA supply_chain_planner_memory CASCADE` on
`production`, redeploy so the SP recreates+owns it, migrate any rows from the orphan `…_memory_app`,
then drop the orphan; or (B) grant the SP full access to the existing schema:
`scripts/grant_lakebase_permissions.py <sp-client-id> --schema supply_chain_planner_memory --mode langgraph`
(SP client id: `databricks apps get <app> -o json | jq -r .service_principal_client_id`).

### P2 stretch — per-session ephemeral branch (sanctioned "stateful agent + branching" demo)

The canonical Lakebase agent demo (Jenny Sun's WIP) forks a **per-session branch** at run start so a
prompt-injected `DROP TABLE` can only nuke the throwaway branch, then merges the "good" memories back
to the parent at commit (branch merge, or an async distillation supervisor). This maps cleanly onto
our HITL → `commit_node` flow and is a strong future enhancement — **not built this phase.**

### What the agent writes/reads (so memory stays high-signal)

- **Write policy is curated by type + verdict** (`agent_server/memory.py:build_memory_writes`):
  - `("approvals", <user>)` — every committed decision (audit), both verdicts.
  - `("preferences", <user>)` and `("supplier_notes", <supplier>)` — only on **approved,
    action-bearing** outcomes.
  - Each value carries a curated `memory_text`; we embed **only that field** (`index=["memory_text"]`)
    so semantic recall matches meaning, not JSON boilerplate.
- **Read** happens in the `hydrate_memory` graph node (after gather, before the planner): scoped
  semantic recall (`MEMORY_RECALL_LIMIT`, `MEMORY_SIMILARITY_THRESHOLD`) injected into the planner
  prompt and recorded in `trace_notes`.

## Run order

```bash
# 0. (once) data/genie/01_create_operational_schema.py  — empty Genie DDL (schema contract)
uv run python data/operational/01_generate_genie_tables.py   # 5 Genie tables + gold helpers (Spark/DBR)
uv run python data/operational/02_pre_seed_pgvector.py       # native quality_incidents + embeddings + HNSW
uv run python data/operational/03_sync_to_lakebase.py        # Synced Tables (relational → Lakebase)
uv run python data/operational/04_verify_hybrid_query.py     # assert the hero scenario + access scoping
# then: data/genie/02_create_genie_space.py                  — Genie space over the populated tables
```

`01` needs Spark (run on Databricks). For autoscaling, one config covers the whole run: set
`LAKEBASE_AUTOSCALING_PROJECT` + `LAKEBASE_AUTOSCALING_BRANCH` (used by `03`'s sync) + a bare
`LAKEBASE_AUTOSCALING_ENDPOINT` id (combined with project/branch so `02`/`04` connect through it),
plus `LAKEBASE_UC_CATALOG` for `03` and the Databricks CLI. (`02`/`04` also accept a full
`projects/<p>/branches/<b>/endpoints/<id>` path, or `LAKEBASE_INSTANCE_NAME` for a provisioned
instance.) `02`/`04` additionally reach the embedding endpoint.

**Sync mode / cost.** The two live tables (`inventory_current`, `open_pos`) default to **Snapshot**
(`LAKEBASE_SYNC_MODE=SNAPSHOT`) — a one-time copy that goes idle, so no always-on DLT pipeline cost;
fine for the static seeded data. For a demo that shows live updates, set `LAKEBASE_SYNC_MODE=CONTINUOUS`
and re-run `03` — it detects the mode change and delete+recreates those two synced tables (there's no
in-place policy update). Flip back to Snapshot the same way after the demo. CDF stays enabled on the
source either way, so the flip is always available.

## Genie evaluation

[`eval_set.py`](eval_set.py) holds certified Q→expected pairs whose expected values are **derived
from `seeds.py`** (so they can't drift): at-risk ranking, SKU-1001 on-hand, open-PO sum, coverage
gap, adhesives suppliers, Q4 open-PO by supplier. The MLflow `mlflow.genai` harness (P2) runs each
through the Genie tool and scores with a deterministic answer-match scorer + an LLM faithfulness
judge — also covering the "operational SQL correctness" eval dimension from the architecture doc.

## Files

`seeds.py` (source of truth) · `_lakebase.py` (psycopg connect + embed helpers) ·
`01_generate_genie_tables.py` · `02_pre_seed_pgvector.py` · `03_sync_to_lakebase.py` ·
`04_verify_hybrid_query.py` · `eval_set.py`. See [`../../docs/architecture.md`](../../docs/architecture.md)
and [`../../CLAUDE.md`](../../CLAUDE.md).
