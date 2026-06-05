# data/knowledge/ — WS3 Knowledge agent pipeline

PDFs → Delta chunks → Vector Search index, all parameterized by `.env`.

## Run order

| # | Script | Where | What it does |
|---|---|---|---|
| 01 | `01_upload_pdfs.py` | **local** (`uv run python`) | Uploads PDFs from `SEED_DATA_PATH` to `/Volumes/{catalog}/{schema}/{volume}/` |
| 02 | `02_parse_and_chunk.py` | **Databricks notebook / job** | Parses with `pypdf`, chunks (800 / 120 overlap), joins `document_metadata.json`, writes `{catalog}.{schema}.knowledge_chunks` Delta w/ CDF on |
| 03 | `03_build_vs_index.py` | **Databricks notebook / job** (or local with VSC client) | Creates VS endpoint + Delta Sync index w/ managed embeddings (`databricks-gte-large-en`) |

After 03, paste the printed `VECTOR_SEARCH_ENDPOINT` and `VECTOR_SEARCH_INDEX` into `.env`.

## Parameters (all in `.env`)

| Env var | Default | Purpose |
|---|---|---|
| `UC_CATALOG` / `UC_SCHEMA` / `UC_VOLUME` | `supply_chain` / `planner` / `documents` | Target UC location |
| `SEED_DATA_PATH` | `../strategic_revenue_demo/seed_data/bronze_documents` | Local PDFs to ingest (01 only) |
| `DATABRICKS_EMBEDDING_ENDPOINT` | `databricks-gte-large-en` | Managed embedding endpoint for the index |
| `VECTOR_SEARCH_ENDPOINT` | `{UC_CATALOG}-vs-endpoint` if unset | VS endpoint (reuses if exists) |
| `VECTOR_SEARCH_INDEX` | `{UC_CATALOG}.{UC_SCHEMA}.knowledge_chunks_index` if unset | VS index name |
| `DATABRICKS_WAREHOUSE_ID` | unset | Needed by 01 for catalog/schema/volume auto-create |

## Chunks table schema

`{catalog}.{schema}.knowledge_chunks`:

| Column | Type | Notes |
|---|---|---|
| `chunk_id` | STRING | `md5(source || page || content)` — PK |
| `source` | STRING | full UC volume path |
| `filename` | STRING | basename |
| `doc_type` | STRING | `contract` / `supplier_notification` / `competitor_catalog` / `promotion_brief` / `market_event` |
| `doc_id` | STRING | e.g. `CTR-2024-1000` (contracts) or filename stem |
| `page` | INT | 0-based |
| `content` | STRING | chunk text |
| `customer` / `supplier` / `categories` | STRING | joined from `document_metadata.json` when present |
| `parsed_at` | TIMESTAMP | run time |

These columns are the metadata filter surface for the Knowledge tool (`agent_server/tools/knowledge_tool.py`).
