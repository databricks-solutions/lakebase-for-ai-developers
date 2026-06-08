# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Parse & Chunk Bronze PDFs → Delta
# MAGIC
# MAGIC Reads PDFs from the vendored `data/knowledge/bronze_documents/` corpus, parses with `pypdf`,
# MAGIC chunks with `RecursiveCharacterTextSplitter`, joins `document_metadata.json` for business
# MAGIC metadata (customer / supplier / categories), and writes the result to
# MAGIC `{catalog}.{schema}.knowledge_chunks` with CDF enabled (required for the Vector Search
# MAGIC Delta-Sync index in `03_build_vs_index.py`).
# MAGIC
# MAGIC **Runs both ways** — local via Databricks Connect (`uv run python …`) or as a Databricks
# MAGIC notebook/job (ambient `get_spark()`). Reads PDF bytes from the in-repo path; the UC volume
# MAGIC upload in `01_upload_pdfs.py` is for governance / browsing, not required by this script.
# MAGIC
# MAGIC All config from `agent_server.config.settings` — change catalog/schema in `.env`, not here.

# COMMAND ----------
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    REPO_ROOT = str(Path(__file__).resolve().parents[2])
except NameError:
    REPO_ROOT = str(Path.cwd().resolve())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from pyspark.sql.functions import col, concat_ws, current_timestamp, md5

from agent_server.config import settings
from agent_server.contracts import DocType
from data._spark import get_spark

# COMMAND ----------
# Subfolder layout → DocType. Matches the bronze_documents/ source tree.
SUBFOLDER_TO_DOCTYPE: dict[str, DocType] = {
    "contracts": DocType.CONTRACT,
    "supplier_notifications": DocType.SUPPLIER_NOTIFICATION,
    "competitor_catalogs": DocType.COMPETITOR_CATALOG,
    "promotion_briefs": DocType.PROMOTION_BRIEF,
    "market_events": DocType.MARKET_EVENT,
}

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

seed_dir = settings.seed_data_dir  # absolute Path to vendored bronze_documents/
chunks_table = settings.chunks_table

print(f"Seed dir     : {seed_dir}")
print(f"Output table : {chunks_table}")
print(f"Chunk size   : {CHUNK_SIZE} (overlap {CHUNK_OVERLAP})")

# COMMAND ----------
# Load document_metadata.json sidecar: filename → customer/supplier/categories.
metadata_path = seed_dir / "document_metadata.json"
metadata_by_filename: dict[str, dict] = {}
if metadata_path.exists():
    for rec in json.loads(metadata_path.read_text()):
        if "filename" in rec:
            metadata_by_filename[rec["filename"]] = rec
    print(f"Loaded metadata for {len(metadata_by_filename)} files")
else:
    print(f"NOTE: no document_metadata.json at {metadata_path} — chunks won't carry business metadata")


# COMMAND ----------
def doc_id_from_filename(filename: str, doc_type: DocType) -> str:
    """Best-effort stable doc_id. Contracts: 'CTR-2024-1000_Caterpillar_Inc.pdf' → 'CTR-2024-1000'."""
    stem = Path(filename).stem
    if doc_type is DocType.CONTRACT and "_" in stem:
        return stem.split("_", 1)[0]
    return stem


splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
rows: list[dict] = []
pdf_count = 0
skip_count = 0

for subfolder, doc_type in SUBFOLDER_TO_DOCTYPE.items():
    folder = seed_dir / subfolder
    if not folder.exists():
        print(f"  skip {subfolder}/  (not present)")
        continue

    for pdf in folder.rglob("*.pdf"):
        try:
            reader = PdfReader(str(pdf))
        except Exception as e:
            print(f"  WARN  could not parse {pdf}: {e}")
            skip_count += 1
            continue
        pdf_count += 1
        meta = metadata_by_filename.get(pdf.name, {})
        doc_id = doc_id_from_filename(pdf.name, doc_type)

        for page_idx, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if not text.strip():
                continue
            for chunk in splitter.split_text(text):
                rows.append(
                    {
                        "source": f"{subfolder}/{pdf.relative_to(folder)}",
                        "filename": pdf.name,
                        "doc_type": doc_type.value,
                        "doc_id": doc_id,
                        "page": page_idx,
                        "content": chunk,
                        "customer": meta.get("customer"),
                        "supplier": meta.get("supplier"),
                        "categories": ",".join(meta.get("categories", [])) or None,
                    }
                )

print(f"\nParsed {pdf_count} PDFs → {len(rows)} chunks  ({skip_count} skipped)")

# COMMAND ----------
if not rows:
    raise SystemExit(f"No chunks produced — is the seed dir populated? {seed_dir}")

spark = get_spark()
df = (
    spark.createDataFrame(rows)
    .withColumn("chunk_id", md5(concat_ws("||", col("source"), col("page"), col("content"))))
    .withColumn("parsed_at", current_timestamp())
)

(
    df.write.mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(chunks_table)
)

# CDF is required for the Vector Search Delta-Sync index in 03_build_vs_index.py.
spark.sql(f"ALTER TABLE {chunks_table} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

count = spark.table(chunks_table).count()
print(f"\nWrote {count} chunks → {chunks_table} (CDF enabled)")

# COMMAND ----------
spark.table(chunks_table).limit(5).show(truncate=80)
