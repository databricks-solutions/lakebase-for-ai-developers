# data/genie/ — WS3 Analytics agent (Genie) plumbing

Creates the operational schema and the Genie space programmatically. Chandhana (WS2) tunes
the table contents + sample questions later; this scaffolds the substrate.

## Run order

| # | Script | Where | What it does |
|---|---|---|---|
| 01 | `01_create_operational_schema.py` | **Databricks notebook / job** | Creates 5 empty Delta tables with comments — Genie reads the COMMENTs as metadata |
| 02 | `02_create_genie_space.py` | **local** (`uv run python`) or notebook | Calls `w.genie.create_space(...)` with the serialized space from `genie_config.py`. Idempotent — finds existing by title. |

After 02, paste the printed `space_id` into `.env` as `GENIE_SPACE_ID`.

## Files

- **`genie_config.py`** — canonical config for the space (tables, description, sample
  questions, instructions, certified example SQLs). **Edit this, not the UI.** Re-running
  `02_create_genie_space.py` updates the live space to match.
- **`01_create_operational_schema.py`** — empty DDL. WS2 (Chandhana) replaces the contents
  via dbldatagen / Faker; schema stays put as the contract.
- **`02_create_genie_space.py`** — REST call via `databricks-sdk >= 0.106.0`. Falls back to
  printing the config block if the SDK doesn't have the method.

## Parameters (all in `.env`)

| Env var | Default | Purpose |
|---|---|---|
| `UC_CATALOG` / `UC_SCHEMA` | `supply_chain` / `planner` | Target UC schema for the tables |
| `DATABRICKS_WAREHOUSE_ID` | unset → auto-detect | Genie needs a warehouse to execute SQL |
| `GENIE_SPACE_ID` | (output of 02) | Used by the agent's Genie tool at runtime |

## Tables created (all empty until WS2)

| Table | Purpose |
|---|---|
| `suppliers` | Master data — supplier_id, name, country, categories |
| `product_dim` | SKU master — sku, name, category, list_price |
| `inventory` | On-hand per SKU per location |
| `purchase_orders` | Open + historical POs |
| `supplier_status` | Rolling supplier risk + on-time score |

Schema rationale + Genie-facing comments live in the DDL — they're the LLM's grounding signal.
