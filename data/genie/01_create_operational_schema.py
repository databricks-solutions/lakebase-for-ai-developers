# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Create the Operational Schema (empty tables) for the Genie space
# MAGIC
# MAGIC Stands up empty Delta tables matching `genie_config.SUPPLY_CHAIN_GENIE_SPACE.tables` so
# MAGIC the Genie space (`02_create_genie_space.py`) has something to bind to, even before WS2
# MAGIC fills these with synthetic data. Column comments mirror the Genie table descriptions —
# MAGIC the Genie LLM uses them for NL→SQL grounding.
# MAGIC
# MAGIC Idempotent. Re-runs are a no-op (CREATE TABLE IF NOT EXISTS). Safe to run as Chandhana's
# MAGIC WS2 work lands — she'll either ALTER TABLE or REPLACE TABLE to evolve the schema.
# MAGIC
# MAGIC Run as a notebook or job on Databricks.

# COMMAND ----------
import sys
from pathlib import Path

try:
    REPO_ROOT = str(Path(__file__).resolve().parents[2])
except NameError:
    REPO_ROOT = str(Path.cwd().resolve())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent_server.config import settings

# COMMAND ----------
CATALOG = settings.uc_catalog
SCHEMA = settings.uc_schema
PREFIX = f"`{CATALOG}`.`{SCHEMA}`"

print(f"Target schema: {CATALOG}.{SCHEMA}")

spark.sql(f"CREATE CATALOG IF NOT EXISTS `{CATALOG}`")  # noqa: F821
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {PREFIX}")  # noqa: F821

# COMMAND ----------
DDL_SUPPLIERS = f"""
    CREATE TABLE IF NOT EXISTS {PREFIX}.suppliers (
      supplier_id STRING NOT NULL COMMENT 'PK; e.g. SUP-001',
      name        STRING COMMENT 'Display name of the supplier',
      country     STRING COMMENT 'ISO-3166 country name',
      categories  STRING COMMENT 'Comma-separated product categories (e.g. adhesives,fasteners)'
    )
    USING DELTA
    COMMENT 'Master data for upstream suppliers. PK: supplier_id.'
"""

DDL_PRODUCT_DIM = f"""
    CREATE TABLE IF NOT EXISTS {PREFIX}.product_dim (
      sku        STRING NOT NULL COMMENT 'PK; e.g. SKU-1001',
      name       STRING COMMENT 'Product display name',
      category   STRING COMMENT 'Product category (adhesives, fasteners, abrasives, ...)',
      list_price DOUBLE COMMENT 'List price per unit in USD'
    )
    USING DELTA
    COMMENT 'SKU master. PK: sku.'
"""

DDL_INVENTORY = f"""
    CREATE TABLE IF NOT EXISTS {PREFIX}.inventory (
      sku          STRING NOT NULL COMMENT 'FK → product_dim.sku',
      location     STRING NOT NULL COMMENT 'Warehouse / DC code',
      on_hand_qty  DOUBLE COMMENT 'Units currently on hand',
      last_updated TIMESTAMP COMMENT 'When this row was last refreshed'
    )
    USING DELTA
    COMMENT 'Current on-hand stock per SKU per location. Use MAX(last_updated) for snapshot.'
"""

DDL_PURCHASE_ORDERS = f"""
    CREATE TABLE IF NOT EXISTS {PREFIX}.purchase_orders (
      po_id         STRING NOT NULL COMMENT 'PK; e.g. PO-2026-0001',
      supplier_id   STRING NOT NULL COMMENT 'FK → suppliers.supplier_id',
      sku           STRING NOT NULL COMMENT 'FK → product_dim.sku',
      qty           DOUBLE COMMENT 'Units on the PO',
      expected_date DATE COMMENT 'Expected delivery date',
      status        STRING COMMENT 'open | in_transit | delivered | cancelled'
    )
    USING DELTA
    COMMENT 'Open and historical POs. Filter status != cancelled for fulfillment math.'
"""

DDL_SUPPLIER_STATUS = f"""
    CREATE TABLE IF NOT EXISTS {PREFIX}.supplier_status (
      supplier_id  STRING NOT NULL COMMENT 'FK → suppliers.supplier_id',
      status       STRING COMMENT 'healthy | watch | at_risk',
      risk_score   DOUBLE COMMENT 'Composite risk score 0-100; higher is worse',
      last_updated TIMESTAMP COMMENT 'When this rating was assigned'
    )
    USING DELTA
    COMMENT 'Rolling supplier risk + on-time score. Use MAX(last_updated) for current state.'
"""

for ddl in (DDL_SUPPLIERS, DDL_PRODUCT_DIM, DDL_INVENTORY, DDL_PURCHASE_ORDERS, DDL_SUPPLIER_STATUS):
    spark.sql(ddl)  # noqa: F821

# COMMAND ----------
# Verify
for table in ("suppliers", "product_dim", "inventory", "purchase_orders", "supplier_status"):
    fq = f"{CATALOG}.{SCHEMA}.{table}"
    cnt = spark.table(fq).count()  # noqa: F821
    print(f"  {fq}: {cnt} rows")

print("\nDone. WS2 (Chandhana) will populate these tables with synthetic data.")
