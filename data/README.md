# data/ — WS2 (Operational data) + WS4 (demo dataset)

> **Scaffold placeholder.** No data-gen code yet. This is the home for synthetic data
> generation, the operational tables, the hybrid similarity+join query, and the seeded **Acme**
> demo scenario.

> **Run it both ways.** Write data-gen / query code so the *same* file runs in the IDE (loads
> `.env` + `DATABRICKS_CONFIG_PROFILE`) and in a Databricks notebook/job (ambient auth). Only
> `load_dotenv()` when `DATABRICKS_RUNTIME_VERSION` is unset. See
> [`../CLAUDE.md` → Running locally vs. on Databricks](../CLAUDE.md#running-locally-vs-on-databricks-auth--config).

## What lives here (to build)

1. **Synthetic supply-chain dataset** — `suppliers`, `inventory`, `purchase_orders`,
   `supplier_status`, and unstructured `incidents` / quality issues. Use Databricks Labs
   [`dbldatagen`](https://github.com/databrickslabs/dbldatagen) (Spark-native, scales) or
   `Faker` for quick local generation. Write to Delta in UC.
2. **Delta → Lakebase** — sync operational tables via **Synced Tables** (Continuous for fast
   inventory/POs, Snapshot for slow product dims) so the joins below hit fresh OLTP rows.
3. **Memory vectors are LangGraph-managed.** Long-term memory + semantic search go through the
   `AsyncDatabricksStore` (embeddings configured via `DATABRICKS_EMBEDDING_ENDPOINT`); the store
   owns the vector tables/index. Don't hand-manage a pgvector client for memory. See
   [`../CLAUDE.md` → State & memory](../CLAUDE.md#state--memory-all-on-lakebase-managed-by-langgraph).
4. **Hybrid query (the differentiator)** — for the canonical scenario, query Lakebase directly so
   vector similarity + access predicate (`product_code IN planner_acl`) + JOIN to
   `inventory ⨝ purchase_orders ⨝ supplier_status` resolve in ONE governed SQL statement. The
   agent should **return this SQL** for traceability.
5. **Acme demo scenario** — seed deterministic rows so the canonical request reproduces.

## Skills & references
Vendored `lakebase-setup`, `agent-memory`, `create-tools`, `add-tools`; plus `databricks-lakebase`.
See [`../docs/references.md`](../docs/references.md).

## Risk note
WS2 **spikes the hybrid query first** (Day 1). If it's slow/fails by the Day-2 trigger, fall
back to a single-query join with app-side access scoping. See [`../docs/sprint-plan.md`](../docs/sprint-plan.md).
