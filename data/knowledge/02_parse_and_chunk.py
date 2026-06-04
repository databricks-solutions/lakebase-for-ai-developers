# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Parse & Chunk Bronze PDFs → Delta
# MAGIC
# MAGIC Reads every PDF under `/Volumes/{catalog}/{schema}/{volume}/` (uploaded by `01_upload_pdfs.py`),
# MAGIC parses with `pypdf`, chunks with `RecursiveCharacterTextSplitter`, joins the
# MAGIC `document_metadata.json` sidecar for business metadata (customer / supplier / categories),
# MAGIC and writes the result to `{catalog}.{schema}.knowledge_chunks` with CDF enabled (required
# MAGIC for the Vector Search Delta Sync index built in `03_build_vs_index.py`).
# MAGIC
# MAGIC **Run this as a notebook or a job on Databricks.** It uses the `spark`/`dbutils` globals
# MAGIC and is not designed for local execution (parsing is local-compatible, but the Delta write
# MAGIC requires Spark — submit via `databricks bundle run` or open in a workspace notebook).
# MAGIC
# MAGIC All config is read from `agent_server.config.settings` (env-aware), so changing the
# MAGIC catalog/schema/volume is a `.env` edit, not a code edit.

# COMMAND ----------
# MAGIC %pip install -q pypdf langchain-text-splitters pydantic
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
import json
import sys
from pathlib import Path

# Make the agent_server package importable. As a workspace file, `__file__` resolves to the
# notebook path and we can walk up to the repo root. In ad-hoc notebook runs without
# `__file__`, fall back to cwd (assumes the notebook is opened from the repo root).
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

# COMMAND ----------
# Subfolder layout in the volume → DocType. Matches the bronze_documents/ source tree.
SUBFOLDER_TO_DOCTYPE: dict[str, DocType] = {
    "contracts": DocType.CONTRACT,
    "supplier_notifications": DocType.SUPPLIER_NOTIFICATION,
    "competitor_catalogs": DocType.COMPETITOR_CATALOG,
    "promotion_briefs": DocType.PROMOTION_BRIEF,
    "market_events": DocType.MARKET_EVENT,
}

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

volume_uri = settings.volume_uri
chunks_table = settings.chunks_table

print(f"Volume       : {volume_uri}")
print(f"Output table : {chunks_table}")
print(f"Chunk size   : {CHUNK_SIZE} (overlap {CHUNK_OVERLAP})")

# COMMAND ----------
# Load the document_metadata.json sidecar: maps filename → customer/supplier/categories.
# Missing-file is fine (chunks just won't be enriched with those columns).
metadata_path = f"{volume_uri}/document_metadata.json"
metadata_by_filename: dict[str, dict] = {}
try:
    with open(metadata_path) as fh:
        for rec in json.load(fh):
            if "filename" in rec:
                metadata_by_filename[rec["filename"]] = rec
    print(f"Loaded metadata for {len(metadata_by_filename)} files")
except FileNotFoundError:
    print(f"NOTE: no document_metadata.json at {metadata_path} — chunks won't carry business metadata")

# COMMAND ----------
def doc_id_from_filename(filename: str, doc_type: DocType) -> str:
    """Best-effort stable doc_id. Contracts: 'CTR-2024-1000_Caterpillar_Inc.pdf' → 'CTR-2024-1000'.
    Everything else: filename stem."""
    stem = Path(filename).stem
    if doc_type is DocType.CONTRACT and "_" in stem:
        return stem.split("_", 1)[0]
    return stem


splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
rows: list[dict] = []
pdf_count = 0

for subfolder, doc_type in SUBFOLDER_TO_DOCTYPE.items():
    folder = f"{volume_uri}/{subfolder}"
    try:
        listing = dbutils.fs.ls(folder)  # noqa: F821 — notebook global
    except Exception as e:
        print(f"  skip {subfolder}/  ({e.__class__.__name__})")
        continue

    for entry in listing:
        if not entry.name.lower().endswith(".pdf"):
            continue
        local_path = entry.path.replace("dbfs:", "")
        try:
            reader = PdfReader(local_path)
        except Exception as e:
            print(f"  WARN  could not parse {entry.path}: {e}")
            continue
        pdf_count += 1
        meta = metadata_by_filename.get(entry.name, {})
        doc_id = doc_id_from_filename(entry.name, doc_type)

        for page_idx, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if not text.strip():
                continue
            for chunk in splitter.split_text(text):
                rows.append(
                    {
                        "source": entry.path,
                        "filename": entry.name,
                        "doc_type": doc_type.value,
                        "doc_id": doc_id,
                        "page": page_idx,
                        "content": chunk,
                        "customer": meta.get("customer"),
                        "supplier": meta.get("supplier"),
                        "categories": ",".join(meta.get("categories", [])) or None,
                    }
                )

print(f"\nParsed {pdf_count} PDFs → {len(rows)} chunks")

# COMMAND ----------
if not rows:
    raise SystemExit(f"No chunks produced — is the volume populated? Check: {volume_uri}")

df = (
    spark.createDataFrame(rows)  # noqa: F821 — notebook global
    .withColumn("chunk_id", md5(concat_ws("||", col("source"), col("page"), col("content"))))
    .withColumn("parsed_at", current_timestamp())
)

(
    df.write.mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(chunks_table)
)

# CDF is required for the Vector Search Delta Sync index in 03_build_vs_index.py.
spark.sql(  # noqa: F821 — notebook global
    f"ALTER TABLE {chunks_table} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)"
)

count = spark.table(chunks_table).count()  # noqa: F821 — notebook global
print(f"\nWrote {count} chunks → {chunks_table} (CDF enabled)")

# COMMAND ----------
display(spark.table(chunks_table).limit(5))  # noqa: F821 — notebook global
