# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — Welcome: Meet the Supply-Chain Planner Copilot
# MAGIC
# MAGIC This is a five-notebook guided tour of a multi-agent Supply-Chain Planner Copilot — a
# MAGIC production-shaped reference build for **stateful agents on Databricks with Lakebase**. Every
# MAGIC notebook in this tour calls the same functions the deployed app uses; nothing here is a
# MAGIC simplified reimplementation.
# MAGIC
# MAGIC One note before diving in: the storyboard behind this demo (`docs/storyboard.md`) frames its
# MAGIC buying persona as **ML / AI Platform Engineers who build production agents** — and explicitly
# MAGIC excludes notebook-based Data Scientists. This tour is a notebook anyway, on purpose: it's the
# MAGIC "pop the hood" artifact for that production-agent audience, not a return to notebook-driven
# MAGIC data science.
# MAGIC
# MAGIC ## The hero scenario
# MAGIC
# MAGIC Every notebook in this tour anchors on the same seeded scenario:
# MAGIC
# MAGIC - **Henkel AG** (`SUP-001`) supplies **Structural Epoxy Adhesive** (`SKU-1001`) and is
# MAGIC   currently flagged **`at_risk`**.
# MAGIC - On-hand inventory is only **40 units**.
# MAGIC - Two open POs are inbound: **500 units from Henkel** (the same, at-risk source) and
# MAGIC   **300 units from DuPont** (`SUP-002`, a healthy alternate source) — 800 units incoming
# MAGIC   against a 40-unit on-hand position, but 500 of those 800 ride on the same supplier that's
# MAGIC   currently flagged at risk.
# MAGIC - There's a recurring cluster of past quality incidents on this exact SKU/supplier pair —
# MAGIC   adhesive that cracks under load and fails the pull test.
# MAGIC
# MAGIC That's the setup the planner has to reason about: a coverage gap, a risky supplier, and a
# MAGIC quality history — spread across structured tables, an operational Postgres store, and an
# MAGIC unstructured document corpus.
# MAGIC
# MAGIC ## The architecture
# MAGIC
# MAGIC ```mermaid
# MAGIC flowchart LR
# MAGIC   subgraph Build["Build time (seed job)"]
# MAGIC     seed["setup_and_seed job"]
# MAGIC     uc[("UC Delta<br/>suppliers / inventory / POs")]
# MAGIC     lb[("Lakebase Postgres<br/>public + memory + app")]
# MAGIC     genie(["Genie space"])
# MAGIC     vs[("Vector Search<br/>knowledge_chunks_index")]
# MAGIC     seed --> uc
# MAGIC     seed --> lb
# MAGIC     seed --> genie
# MAGIC     seed --> vs
# MAGIC   end
# MAGIC
# MAGIC   subgraph App["Runtime · Agent (Databricks App)"]
# MAGIC     lg["LangGraph supervisor graph"]
# MAGIC     fastapi["FastAPI /api/* + /invocations"]
# MAGIC     mlflow[["MLflow traces (UC)"]]
# MAGIC     lg --> fastapi
# MAGIC     lg --> mlflow
# MAGIC   end
# MAGIC
# MAGIC   subgraph FE["Runtime · Frontend"]
# MAGIC     spa["Vite + React SPA at /ui"]
# MAGIC   end
# MAGIC
# MAGIC   lb -->|"read + write state"| lg
# MAGIC   genie -->|"NL to SQL"| lg
# MAGIC   vs -->|"passages"| lg
# MAGIC   uc -.->|"synced to"| lb
# MAGIC   spa <-->|"SSE /api/chat/stream"| fastapi
# MAGIC ```
# MAGIC
# MAGIC *(Source: `docs/architecture.md`, Diagram 1 — "End-to-end overview".)*
# MAGIC
# MAGIC ## What you'll see
# MAGIC
# MAGIC 1. **`01_data_and_lakebase.py`** — the same hero numbers, three ways: governed Delta, a live
# MAGIC    Lakebase Postgres mirror, and a native pgvector table — ending on the one governed SQL
# MAGIC    query that joins similarity search to live inventory and PO data.
# MAGIC 2. **`02_genie_and_vector_search.py`** — the other two retrieval engines: Genie for NL→SQL
# MAGIC    analytics, Vector Search for the unstructured knowledge corpus.
# MAGIC 3. **`03_agent_end_to_end.py`** — the real LangGraph agent: routing, an MLflow trace that
# MAGIC    renders itself inline, a human-in-the-loop pause and approval, and short-term
# MAGIC    (same-thread) state.
# MAGIC 4. **`04_long_term_memory.py`** — a brand-new thread proving the agent recalls a decision
# MAGIC    across sessions, backed by nothing but Postgres.

# COMMAND ----------
# MAGIC %md
# MAGIC ### Configure this notebook
# MAGIC These two widgets map to `agent_server.config.settings` (`UC_CATALOG` / `UC_SCHEMA`). Change
# MAGIC a value, then **Run ▸ Clear State and Run All** — settings are resolved once per session, not
# MAGIC per cell. Leave the defaults if you deployed with the standard `dev` target.

# COMMAND ----------
import sys
from pathlib import Path

try:
    REPO_ROOT = str(Path(__file__).resolve().parents[1])
except NameError:
    REPO_ROOT = str(Path.cwd().resolve())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from databricks.sdk.runtime import dbutils
    dbutils.widgets.text("UC_CATALOG", "supply_chain", "UC Catalog")
    dbutils.widgets.text("UC_SCHEMA", "planner", "UC Schema")
except Exception:
    pass  # no notebook context (e.g. local `python file.py`) — .env / defaults apply instead

# COMMAND ----------
from agent_server.config import settings  # picks up the widgets above
from data.operational import seeds

print(f"UC catalog             : {settings.uc_catalog}")
print(f"UC schema               : {settings.uc_schema}")
print(f"Lakebase memory schema  : {settings.lakebase_memory_schema}")
print(f"Lakebase operational schema : {settings.lakebase_operational_schema}")
print()
print(f"Hero supplier : {seeds.HERO_SUPPLIER_ID} (alt: {seeds.ALT_SUPPLIER_ID})")
print(f"Hero SKU      : {seeds.HERO_SKU}")
print(f"On-hand       : {seeds.HERO_ON_HAND}")
print(f"Open POs      : {seeds.HERO_HENKEL_OPEN_PO_QTY} (Henkel) + {seeds.HERO_DUPONT_OPEN_PO_QTY} (DuPont)")

# COMMAND ----------
# MAGIC %md
# MAGIC ### Sanity check — is the demo data seeded?
# MAGIC If this fails, run `make deploy PROFILE=<p>` first (see the repo root README) — the rest of
# MAGIC this tour reads real seeded data, not fixtures.

# COMMAND ----------
try:
    from data._spark import get_spark

    spark = get_spark()
    count = spark.table(f"{settings.uc_catalog}.{settings.uc_schema}.suppliers").count()
    print(f"✓ Found {count} rows in {settings.uc_catalog}.{settings.uc_schema}.suppliers — ready to go.")
except Exception as exc:
    print(f"✗ Could not read the suppliers table: {exc}")
    print("  Run `make deploy PROFILE=<p>` first, or check the UC_CATALOG/UC_SCHEMA widgets above.")

# COMMAND ----------
# MAGIC %md
# MAGIC Next up: **`01_data_and_lakebase.py`**.
