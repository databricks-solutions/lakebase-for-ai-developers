# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Data Insights: Delta, Lakebase, and pgvector
# MAGIC
# MAGIC One governed backend, three layers. This notebook shows the same hero numbers three times —
# MAGIC as governed Delta tables, as a live Lakebase Postgres mirror, and as a native pgvector table —
# MAGIC then ends on the payoff: **one SQL statement** that resolves semantic similarity and live
# MAGIC operational joins together.
# MAGIC
# MAGIC All reads. Nothing in this notebook writes anything.

# COMMAND ----------
# MAGIC %md
# MAGIC ### Install psycopg (serverless compute only)
# MAGIC Serverless compute doesn't ship `psycopg` — the `connect()` calls in Layers 2-3 need it.
# MAGIC Install once per session, then restart Python so the import picks it up. Skip this cell on
# MAGIC a classic cluster that already has it, or running locally (`uv sync` already installed it).

# COMMAND ----------
# MAGIC %pip install psycopg "databricks-sdk>=0.118.0" "databricks-ai-bridge[agent-server]" databricks-langchain
# MAGIC %restart_python

# COMMAND ----------
# MAGIC %md
# MAGIC ### Configure this notebook
# MAGIC Change a value, then **Run ▸ Clear State and Run All** (settings resolve once per session).
# MAGIC `LAKEBASE_AUTOSCALING_ENDPOINT` is required — `connect()` has no other way to find your
# MAGIC Lakebase project on a fresh cluster/serverless session (no `.env` there). Leave
# MAGIC `PROJECT`/`BRANCH` blank only if the endpoint value is already a full
# MAGIC `projects/<p>/branches/<b>/endpoints/<id>` path; otherwise set all three.

# COMMAND ----------
import sys
from pathlib import Path

try:
    _start = Path(__file__).resolve().parent
except NameError:
    _start = Path.cwd().resolve()  # notebook UI: no __file__; cwd is this notebook's own dir
REPO_ROOT = str(next((p for p in (_start, *_start.parents) if (p / "pyproject.toml").exists()), _start))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent_server.config import settings

try:
    from databricks.sdk.runtime import dbutils, display
    dbutils.widgets.text("UC_CATALOG", settings.uc_catalog, "UC Catalog")
    dbutils.widgets.text("UC_SCHEMA", settings.uc_schema, "UC Schema")
    dbutils.widgets.text("LAKEBASE_OPERATIONAL_SCHEMA", settings.lakebase_operational_schema, "Lakebase operational schema")
    dbutils.widgets.text("LAKEBASE_AUTOSCALING_ENDPOINT", settings.lakebase_autoscaling_endpoint or "", "Lakebase endpoint (id or full path)")
    dbutils.widgets.text("LAKEBASE_AUTOSCALING_PROJECT", settings.lakebase_autoscaling_project or "", "Lakebase project (optional)")
    dbutils.widgets.text("LAKEBASE_AUTOSCALING_BRANCH", settings.lakebase_autoscaling_branch or "", "Lakebase branch (optional)")
    UC_CATALOG = dbutils.widgets.get("UC_CATALOG")
    UC_SCHEMA = dbutils.widgets.get("UC_SCHEMA")
    LAKEBASE_OPERATIONAL_SCHEMA = dbutils.widgets.get("LAKEBASE_OPERATIONAL_SCHEMA")
    LAKEBASE_AUTOSCALING_ENDPOINT = dbutils.widgets.get("LAKEBASE_AUTOSCALING_ENDPOINT")
    LAKEBASE_AUTOSCALING_PROJECT = dbutils.widgets.get("LAKEBASE_AUTOSCALING_PROJECT")
    LAKEBASE_AUTOSCALING_BRANCH = dbutils.widgets.get("LAKEBASE_AUTOSCALING_BRANCH")
except Exception:
    # no notebook context (e.g. local `python file.py`) — .env / defaults apply instead
    UC_CATALOG = UC_SCHEMA = LAKEBASE_OPERATIONAL_SCHEMA = None
    LAKEBASE_AUTOSCALING_ENDPOINT = None
    LAKEBASE_AUTOSCALING_PROJECT = LAKEBASE_AUTOSCALING_BRANCH = None
    display = None

# COMMAND ----------
from data._spark import get_spark
from data.operational import seeds
from data.operational._lakebase import connect

if UC_CATALOG:
    settings.uc_catalog = UC_CATALOG
if UC_SCHEMA:
    settings.uc_schema = UC_SCHEMA
if LAKEBASE_OPERATIONAL_SCHEMA:
    settings.lakebase_operational_schema = LAKEBASE_OPERATIONAL_SCHEMA
if LAKEBASE_AUTOSCALING_ENDPOINT:
    settings.lakebase_autoscaling_endpoint = LAKEBASE_AUTOSCALING_ENDPOINT
if LAKEBASE_AUTOSCALING_PROJECT:
    settings.lakebase_autoscaling_project = LAKEBASE_AUTOSCALING_PROJECT
if LAKEBASE_AUTOSCALING_BRANCH:
    settings.lakebase_autoscaling_branch = LAKEBASE_AUTOSCALING_BRANCH


def show_rows(cur, label: str) -> None:
    """Render a cursor's last result set: a table via notebook `display()`, or a plain print
    outside a notebook (local `python file.py`, no `display` in scope)."""
    rows = cur.fetchall()
    if display:
        import pandas as pd
        display(pd.DataFrame(rows, columns=[c.name for c in cur.description]))
    else:
        print(f"{label}:", rows)


spark = get_spark()
print(f"UC catalog/schema        : {settings.uc_catalog}.{settings.uc_schema}")
print(f"Lakebase operational schema : {settings.lakebase_operational_schema}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Layer 1 — Delta: the governed source of truth
# MAGIC
# MAGIC `inventory_current` and `open_pos` are the two live "gold" tables that get synced to Lakebase
# MAGIC (see Layer 2 below); `supplier_status` tracks the rolling risk rating.

# COMMAND ----------
delta_prefix = f"{settings.uc_catalog}.{settings.uc_schema}"

on_hand = spark.sql(
    f"SELECT sku, on_hand_qty FROM {delta_prefix}.inventory_current WHERE sku = '{seeds.HERO_SKU}'"
)
open_pos = spark.sql(
    f"""
    SELECT supplier_id, sku, open_po_qty, next_expected_date
    FROM {delta_prefix}.open_pos
    WHERE sku = '{seeds.HERO_SKU}'
    ORDER BY supplier_id
    """
)
status = spark.sql(
    f"""
    SELECT supplier_id, status, risk_score, last_updated
    FROM {delta_prefix}.supplier_status
    WHERE supplier_id = '{seeds.HERO_SUPPLIER_ID}'
    ORDER BY last_updated
    """
)

print(f"On-hand for {seeds.HERO_SKU}:")
on_hand.show(truncate=False)
print("Open POs for the same SKU:")
open_pos.show(truncate=False)
print(f"Risk history for {seeds.HERO_SUPPLIER_ID} (Henkel):")
status.show(truncate=False)

total_open_po = seeds.HERO_HENKEL_OPEN_PO_QTY + seeds.HERO_DUPONT_OPEN_PO_QTY
print(
    f"{seeds.HERO_ON_HAND:.0f} on-hand, {total_open_po:.0f} incoming, "
    f"a {total_open_po - seeds.HERO_ON_HAND:.0f}-unit net gap — and 500 of that 800 rides on the "
    "supplier flagged at_risk above."
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Layer 2 — Lakebase Synced Tables: the same rows, now live in Postgres
# MAGIC
# MAGIC `inventory_current`, `open_pos`, `suppliers`, `product_dim`, and `supplier_status` are
# MAGIC CDF-enabled Delta tables mirrored **read-only** into Lakebase Postgres via Synced Tables — no
# MAGIC custom pipeline, just a managed Delta → Postgres mirror. Same numbers, different backend.

# COMMAND ----------
with connect() as conn, conn.cursor() as cur:
    schema = settings.lakebase_operational_schema
    cur.execute(f"SELECT sku, on_hand_qty FROM {schema}.inventory_current WHERE sku = %s", (seeds.HERO_SKU,))
    show_rows(cur, f"Postgres {schema}.inventory_current")

    cur.execute(
        f"SELECT supplier_id, sku, open_po_qty FROM {schema}.open_pos WHERE sku = %s ORDER BY supplier_id",
        (seeds.HERO_SKU,),
    )
    show_rows(cur, f"Postgres {schema}.open_pos")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Layer 3 — native pgvector: the one table that isn't synced
# MAGIC
# MAGIC `quality_incidents` holds a `vector(1024)` embedding column — Delta's `array<float>` syncs to
# MAGIC Postgres `jsonb` (not a real `vector`), and Synced Tables are read-only anyway, so this table
# MAGIC can't ride a sync. It's written directly into Lakebase via psycopg
# MAGIC (`data/operational/02_pre_seed_pgvector.py`), with an HNSW cosine index for fast similarity
# MAGIC search.

# COMMAND ----------
with connect() as conn, conn.cursor() as cur:
    schema = settings.lakebase_operational_schema
    cur.execute(f"SELECT count(*) FROM {schema}.quality_incidents WHERE expired_at IS NULL")
    active_count = cur.fetchone()[0]
    print(f"{active_count} active quality incidents in {schema}.quality_incidents.")

    cur.execute(
        f"""
        SELECT incident_id, supplier_id, sku, category, severity, summary
        FROM {schema}.quality_incidents
        WHERE supplier_id = %s AND sku = %s AND expired_at IS NULL
        LIMIT 3
        """,
        (seeds.HERO_SUPPLIER_ID, seeds.HERO_SKU),
    )
    show_rows(cur, f"A few on {seeds.HERO_SUPPLIER_ID}/{seeds.HERO_SKU}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Putting it together — the hybrid query
# MAGIC
# MAGIC This is the project's thesis, quoted from `docs/architecture.md`:
# MAGIC
# MAGIC > "similarity as one predicate inside a relational/operational query ... instead of pulling
# MAGIC > IDs from a vector index and round-tripping to Postgres to join and re-filter."
# MAGIC
# MAGIC `query_operational_impl` is the exact function the deployed agent calls. It embeds the
# MAGIC question, runs the hybrid SQL below, and always returns the SQL it ran on `.sql` — for
# MAGIC traceability, not just as a debugging aid.

# COMMAND ----------
from agent_server.tools.operational_tool import HYBRID_SQL, query_operational_impl

print("This is the parameterized SQL every operational query runs (%(q)s is the embedded question):\n")
print(HYBRID_SQL)

# COMMAND ----------
print(f"Question: {seeds.HERO_QUERY_TEXT!r}\n")
result = query_operational_impl(seeds.HERO_QUERY_TEXT)

print("Executed SQL (returned on OperationalResult.sql, same string as above):")
print(result.sql)

print("\nTop matches:")
for row in result.rows:
    print(
        f"  similarity={row.similarity:.3f}  {row.supplier_id}/{row.sku}  "
        f"on_hand={row.on_hand_qty}  open_po={row.open_po_qty}  {row.summary!r}"
    )

# COMMAND ----------
# MAGIC %md
# MAGIC **The aha:** one SQL statement returned similarity-ranked incidents already joined to live
# MAGIC on-hand and open-PO numbers. No app-side stitching, no second round trip — and the exact SQL
# MAGIC that ran is sitting right there on `result.sql`.
# MAGIC
# MAGIC Next up: **`02_genie_and_vector_search.py`** — the other two retrieval engines.
