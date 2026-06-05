# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Build Vector Search Index over knowledge_chunks
# MAGIC
# MAGIC Creates a Vector Search endpoint + Delta Sync index over `{catalog}.{schema}.knowledge_chunks`
# MAGIC (written by `02_parse_and_chunk.py`), using managed embeddings from the endpoint configured
# MAGIC in `DATABRICKS_EMBEDDING_ENDPOINT` (default `databricks-gte-large-en`).
# MAGIC
# MAGIC **Idempotent.** Re-runs reuse an existing endpoint and index; only the index sync is
# MAGIC triggered (Delta Sync auto-picks up CDF changes from 02).
# MAGIC
# MAGIC Run as a notebook or job on Databricks (uses `dbutils` only for sys.path; everything else
# MAGIC is the `databricks-vectorsearch` Python client which works locally too if you prefer).
# MAGIC
# MAGIC On completion, prints the index name to paste into `.env` as `VECTOR_SEARCH_INDEX`.

# COMMAND ----------
# MAGIC %pip install -q databricks-vectorsearch pydantic
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
import sys
import time
from pathlib import Path

try:
    REPO_ROOT = str(Path(__file__).resolve().parents[2])
except NameError:
    REPO_ROOT = str(Path.cwd().resolve())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from databricks.vector_search.client import VectorSearchClient

from agent_server.config import settings

# COMMAND ----------
# Resolve endpoint + index names. Empty env values → derived defaults so the script always runs.
endpoint_name = settings.vector_search_endpoint or f"{settings.uc_catalog}-vs-endpoint"
index_name = settings.vector_search_index or settings.default_index_name
source_table = settings.chunks_table
embedding_endpoint = settings.embedding_endpoint

print(f"VS endpoint        : {endpoint_name}")
print(f"VS index           : {index_name}")
print(f"Source Delta table : {source_table}")
print(f"Embedding endpoint : {embedding_endpoint}")

# COMMAND ----------
vsc = VectorSearchClient(disable_notice=True)

# ── Endpoint (idempotent create) ─────────────────────────────────────────────────────────
existing_endpoints = {e["name"] for e in vsc.list_endpoints().get("endpoints", [])}
if endpoint_name in existing_endpoints:
    print(f"Endpoint exists: {endpoint_name}")
else:
    print(f"Creating endpoint {endpoint_name} (STANDARD)…")
    vsc.create_endpoint(name=endpoint_name, endpoint_type="STANDARD")
    # New endpoints can take 1–10 minutes to come online.
    while True:
        ep = vsc.get_endpoint(endpoint_name)
        state = ep.get("endpoint_status", {}).get("state")
        print(f"  endpoint state: {state}")
        if state == "ONLINE":
            break
        time.sleep(20)

# COMMAND ----------
# ── Index (idempotent create) ────────────────────────────────────────────────────────────
existing_indexes = {
    i["name"] for i in vsc.list_indexes(endpoint_name).get("vector_indexes", [])
}
if index_name in existing_indexes:
    print(f"Index exists: {index_name} — triggering sync")
    vsc.get_index(endpoint_name, index_name).sync()
else:
    print(f"Creating index {index_name}…")
    vsc.create_delta_sync_index(
        endpoint_name=endpoint_name,
        index_name=index_name,
        source_table_name=source_table,
        primary_key="chunk_id",
        embedding_source_column="content",
        embedding_model_endpoint_name=embedding_endpoint,
        pipeline_type="TRIGGERED",
        # Surface the metadata columns the agent uses for filtering / attribution.
        columns_to_sync=[
            "chunk_id",
            "source",
            "filename",
            "doc_type",
            "doc_id",
            "page",
            "content",
            "customer",
            "supplier",
            "categories",
        ],
    )

# COMMAND ----------
# ── Wait for the index to be ready (terminal states accept queries) ──────────────────────
TERMINAL_STATES = {"ONLINE_NO_PENDING_UPDATE", "ONLINE_WITH_PENDING_UPDATE"}

print("\nWaiting for index to come online…")
while True:
    state = vsc.get_index(endpoint_name, index_name).describe().get("status", {}).get("detailed_state")
    print(f"  index state: {state}")
    if state in TERMINAL_STATES:
        break
    time.sleep(30)

print(f"\n✓ Index ready: {index_name}")
print(f"\nAdd these to .env so the Knowledge tool finds the index:")
print(f"  VECTOR_SEARCH_ENDPOINT={endpoint_name}")
print(f"  VECTOR_SEARCH_INDEX={index_name}")

# COMMAND ----------
# Smoke test (any doc_type)
resp = vsc.get_index(endpoint_name, index_name).similarity_search(
    query_text="raw material price increase",
    columns=["doc_type", "doc_id", "filename", "content"],
    num_results=3,
)
print(resp)
