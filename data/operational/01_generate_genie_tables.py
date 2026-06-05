# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Generate the operational structured data (Delta)
# MAGIC
# MAGIC Populates the 5 Genie tables (`suppliers`, `product_dim`, `inventory`, `purchase_orders`,
# MAGIC `supplier_status`) created empty by `data/genie/01_create_operational_schema.py`, and
# MAGIC materializes three gold helper tables the operational hybrid query / Synced Tables use:
# MAGIC `inventory_current`, `open_pos`, `user_access`.
# MAGIC
# MAGIC All rows come from `data/operational/seeds.py` (fixed RNG seed + hand-set hero rows), so this
# MAGIC is deterministic and idempotent — re-running overwrites with identical data. The hero
# MAGIC scenario (Henkel `SUP-001` / `SKU-1001`, on-hand 40, an open PO from Henkel + an alternate
# MAGIC from DuPont) is baked in so the canonical demo reproduces.
# MAGIC
# MAGIC Runs both ways via `get_spark()` — the ambient session on Databricks (notebook/job), or
# MAGIC Databricks Connect locally (`uv run python data/operational/01_generate_genie_tables.py`).
# MAGIC CDF is enabled on the two Continuous-sync sources so `03_sync_to_lakebase.py` can use
# MAGIC Triggered/Continuous mode.

# COMMAND ----------
import sys
from pathlib import Path

try:
    REPO_ROOT = str(Path(__file__).resolve().parents[2])
except NameError:
    REPO_ROOT = str(Path.cwd().resolve())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pyspark.sql.types import (
    DateType,
    DoubleType,
    StructField,
    StructType,
    TimestampType,
    StringType,
)

from agent_server.config import settings
from data._spark import get_spark
from data.operational import seeds
from data.operational._lakebase import resolve_demo_user

# Same code locally (Databricks Connect) and on Databricks (ambient session).
spark = get_spark()

# In-scope planner identity for the ACL — the current user (or DEMO_PLANNER_USER), not hardcoded.
DEMO_USER = resolve_demo_user()
print(f"Demo (in-scope) planner: {DEMO_USER}")

# COMMAND ----------
CATALOG = settings.uc_catalog
SCHEMA = settings.uc_schema
PREFIX = f"`{CATALOG}`.`{SCHEMA}`"
print(f"Target schema: {CATALOG}.{SCHEMA}")

# Create the catalog only if missing — CREATE CATALOG IF NOT EXISTS still requires the
# metastore-level CREATE CATALOG grant even when the catalog exists, which a schema-scoped user
# won't have. Guard on existence so users who can only create schemas can run this.
existing_catalogs = {r.catalog for r in spark.sql("SHOW CATALOGS").collect()}
if CATALOG not in existing_catalogs:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS `{CATALOG}`")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {PREFIX}")


def write_table(name: str, rows: list[dict], schema: StructType, *, enable_cdf: bool = False) -> None:
    """Overwrite a Delta table from a list of dicts using an explicit schema. Idempotent.

    For CDF tables we set `delta.enableChangeDataFeed` as a write option so it's on from version 0
    (not a follow-up ALTER that would leave the initial commit un-tracked). NOTE: a full overwrite
    is a non-incremental change — re-running this after a CONTINUOUS synced table (03) is already
    live forces that sync to re-snapshot, so refresh/recreate the sync after any reseed.
    """
    fq = f"{CATALOG}.{SCHEMA}.{name}"
    df = spark.createDataFrame(rows, schema=schema)
    writer = df.write.mode("overwrite").option("overwriteSchema", "true").format("delta")
    if enable_cdf:
        writer = writer.option("delta.enableChangeDataFeed", "true")
    writer.saveAsTable(fq)
    print(f"  wrote {fq}: {df.count()} rows{' (CDF on)' if enable_cdf else ''}")


# COMMAND ----------
# ── The 5 Genie tables ───────────────────────────────────────────────────────────────────────
write_table(
    "suppliers",
    seeds.SUPPLIERS,
    StructType([
        StructField("supplier_id", StringType(), False),
        StructField("name", StringType(), True),
        StructField("country", StringType(), True),
        StructField("categories", StringType(), True),
    ]),
)

write_table(
    "product_dim",
    seeds.build_products(),
    StructType([
        StructField("sku", StringType(), False),
        StructField("name", StringType(), True),
        StructField("category", StringType(), True),
        StructField("list_price", DoubleType(), True),
    ]),
)

write_table(
    "inventory",
    seeds.build_inventory(),
    StructType([
        StructField("sku", StringType(), False),
        StructField("location", StringType(), False),
        StructField("on_hand_qty", DoubleType(), True),
        StructField("last_updated", TimestampType(), True),
    ]),
)

write_table(
    "purchase_orders",
    seeds.build_purchase_orders(),
    StructType([
        StructField("po_id", StringType(), False),
        StructField("supplier_id", StringType(), False),
        StructField("sku", StringType(), False),
        StructField("qty", DoubleType(), True),
        StructField("expected_date", DateType(), True),
        StructField("status", StringType(), True),
    ]),
)

write_table(
    "supplier_status",
    seeds.build_supplier_status(),
    StructType([
        StructField("supplier_id", StringType(), False),
        StructField("status", StringType(), True),
        StructField("risk_score", DoubleType(), True),
        StructField("last_updated", TimestampType(), True),
    ]),
)

# COMMAND ----------
# ── Gold helper tables (joined by the operational hybrid query; synced to Lakebase) ──────────
# inventory_current / open_pos change with operations → Continuous sync → CDF enabled.
# user_access is a slow ACL mapping → Snapshot sync → no CDF needed.
write_table(
    "inventory_current",
    seeds.build_inventory_current(),
    StructType([
        StructField("sku", StringType(), False),
        StructField("on_hand_qty", DoubleType(), True),
    ]),
    enable_cdf=True,
)

write_table(
    "open_pos",
    seeds.build_open_pos(),
    StructType([
        StructField("supplier_id", StringType(), False),
        StructField("sku", StringType(), False),
        StructField("open_po_qty", DoubleType(), True),
        StructField("next_expected_date", DateType(), True),
    ]),
    enable_cdf=True,
)

write_table(
    "user_access",
    seeds.build_user_access(DEMO_USER),
    StructType([
        StructField("user_id", StringType(), False),
        StructField("scope", StringType(), False),
    ]),
)

# COMMAND ----------
# ── Verify: row counts, hero values, FK integrity ───────────────────────────────────────────
for t in ("suppliers", "product_dim", "inventory", "purchase_orders", "supplier_status",
          "inventory_current", "open_pos", "user_access"):
    cnt = spark.table(f"{CATALOG}.{SCHEMA}.{t}").count()  # noqa: F821
    print(f"  {CATALOG}.{SCHEMA}.{t}: {cnt} rows")

hero_sql = f"SELECT on_hand_qty FROM {PREFIX}.inventory_current WHERE sku = '{seeds.HERO_SKU}'"
hero_on_hand = spark.sql(hero_sql).collect()  # noqa: F821
print(f"\nHero {seeds.HERO_SKU} on-hand (expect 40.0): {hero_on_hand[0]['on_hand_qty'] if hero_on_hand else 'MISSING'}")

# FK integrity: every PO (supplier_id, sku) must reference real masters.
orphan_sql = (
    f"SELECT COUNT(*) AS n FROM {PREFIX}.purchase_orders po "
    f"LEFT JOIN {PREFIX}.suppliers s ON s.supplier_id = po.supplier_id "
    f"LEFT JOIN {PREFIX}.product_dim p ON p.sku = po.sku "
    f"WHERE s.supplier_id IS NULL OR p.sku IS NULL"
)
orphans = spark.sql(orphan_sql).collect()[0]["n"]  # noqa: F821
print(f"PO FK orphans (expect 0): {orphans}")

print("\nDone. Next: 02_pre_seed_pgvector.py → 03_sync_to_lakebase.py → 04_verify_hybrid_query.py")
