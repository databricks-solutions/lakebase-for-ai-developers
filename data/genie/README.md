# data/genie/ — WS3 Analytics agent (Genie) plumbing

Holds the canonical Genie-space config and the operational schema DDL. On deploy, the Genie space is
a first-class DABs resource (`resources.genie_spaces` in `databricks.yml`) — created + bound to the
app on `bundle deploy` from a serialized definition generated here. Chandhana (WS2) tunes the table
contents + sample questions later; this scaffolds the substrate.

## Run order

| # | Script | Where | What it does |
|---|---|---|---|
| 01 | `01_create_operational_schema.py` | **Databricks notebook / job** | Creates 5 empty Delta tables with comments — Genie reads the COMMENTs as metadata |
| —  | `build_geniespace_json.py` | **deploy** (`uv run python`, run by `deploy.sh`) | Renders the serialized space from `genie_config.py` → `supply_chain.geniespace.json`, which the `genie_spaces` DABs resource ships via `file_path`. This is the deploy path. |
| 02 | `02_create_genie_space.py` | **local only** (`uv run python`) | SDK `w.genie.create_space(...)` with the same serialized space. Idempotent — finds existing by title. For local experimentation without a bundle deploy. |

On deploy, DABs creates the space and the app reads its id from the resource binding — no manual
`.env` / `GENIE_SPACE_ID` step. (Locally, `02` prints a `space_id` you can paste into `.env`.)

## Files

- **`genie_config.py`** — canonical config for the space (tables, description, sample
  questions, instructions, certified example SQLs). **Edit this, not the UI.** Both the deploy path
  (`build_geniespace_json.py`) and the local path (`02_create_genie_space.py`) render from it.
- **`build_geniespace_json.py`** — renders `genie_config.py` → `supply_chain.geniespace.json` (the
  artifact the `genie_spaces` DABs resource ships via `file_path`). The JSON is **committed** so
  `bundle validate`/`plan`/`deploy` work from a clean checkout; `deploy.sh` regenerates it before
  `bundle deploy` so it tracks `genie_config.py` edits and any `--var uc_catalog/uc_schema` override
  (a custom catalog shows up as a local diff — don't commit it unless you mean to move the default).
- **`01_create_operational_schema.py`** — empty DDL. WS2 (Chandhana) replaces the contents
  via dbldatagen / Faker; schema stays put as the contract.
- **`02_create_genie_space.py`** — local-only SDK creator (`databricks-sdk >= 0.106.0`), for spinning
  up / refreshing a space without a bundle deploy. Not part of the deploy flow.

## Parameters (all in `.env`)

| Env var | Default | Purpose |
|---|---|---|
| `UC_CATALOG` / `UC_SCHEMA` | `supply_chain` / `planner` | Target UC schema for the tables |
| `DATABRICKS_WAREHOUSE_ID` | unset → auto-detect | Genie needs a warehouse to execute SQL |
| `GENIE_SPACE_ID` | injected on deploy from the `genie_spaces` resource | Used by the agent's Genie tool at runtime; set locally only to point a LOCAL app at a specific space |

## Tables created (all empty until WS2)

| Table | Purpose |
|---|---|
| `suppliers` | Master data — supplier_id, name, country, categories |
| `product_dim` | SKU master — sku, name, category, list_price |
| `inventory` | On-hand per SKU per location |
| `purchase_orders` | Open + historical POs |
| `supplier_status` | Rolling supplier risk + on-time score |

Schema rationale + Genie-facing comments live in the DDL — they're the LLM's grounding signal.
