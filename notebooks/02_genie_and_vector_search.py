# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Genie & Vector Search: the other two retrieval engines
# MAGIC
# MAGIC Notebook 01 showed the Lakebase hybrid query — the narrow case where similarity needs to be
# MAGIC one predicate inside a live relational join. Most questions aren't that: they're either
# MAGIC "aggregate my structured data" or "find the passage." This notebook shows both, straight from
# MAGIC `docs/architecture.md`'s routing guidance:
# MAGIC
# MAGIC | Dimension | Mosaic AI Vector Search | Lakebase (LangGraph store + SQL) |
# MAGIC |---|---|---|
# MAGIC | Data nature | large unstructured knowledge corpus | agent memory + operational records co-located with entities |
# MAGIC | Operational join | app-side, multi-hop | native SQL join, single query |
# MAGIC | Scale | large corpora (100Ks–>100M), managed HNSW | small-to-moderate sets co-located with state |
# MAGIC | Managed RAG features | ingestion, hybrid retrieval, reranking | not built-in |
# MAGIC | Output | passages | rows + operational context |
# MAGIC
# MAGIC - **Lakebase + LangGraph** when the agent needs memory + semantic similarity + live SQL joins
# MAGIC   in one operational query path (notebook 01).
# MAGIC - **Mosaic AI Vector Search** for large-scale managed RAG over broad document corpora.
# MAGIC - **Genie** for structured business questions ("total open POs by supplier for Q4?").
# MAGIC
# MAGIC All reads. Nothing in this notebook writes anything.

# COMMAND ----------
# MAGIC %md
# MAGIC ### Configure this notebook
# MAGIC `GENIE_SPACE_ID` and `VECTOR_SEARCH_INDEX` are deploy-generated resource ids with no fixed
# MAGIC default — find them in the deployed App's **Environment Variables** tab (Databricks Apps UI)
# MAGIC or via `databricks bundle summary`. Change a value, then **Run ▸ Clear State and Run All**.

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
    from databricks.sdk.runtime import dbutils
    dbutils.widgets.text("GENIE_SPACE_ID", settings.genie_space_id, "Genie space id")
    dbutils.widgets.text(
        "VECTOR_SEARCH_INDEX", settings.vector_search_index, "Vector Search index (catalog.schema.index)"
    )
    GENIE_SPACE_ID = dbutils.widgets.get("GENIE_SPACE_ID")
    VECTOR_SEARCH_INDEX = dbutils.widgets.get("VECTOR_SEARCH_INDEX")
except Exception:
    GENIE_SPACE_ID = None
    VECTOR_SEARCH_INDEX = None  # no notebook context (e.g. local `python file.py`) — .env / defaults apply instead

# COMMAND ----------
if GENIE_SPACE_ID:
    settings.genie_space_id = GENIE_SPACE_ID
if VECTOR_SEARCH_INDEX:
    settings.vector_search_index = VECTOR_SEARCH_INDEX

print(f"Genie space id       : {settings.genie_space_id or '(not set — the Genie cell below will note this)'}")
print(f"Vector Search index  : {settings.vector_search_index or settings.default_index_name}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Genie — natural language over governed Delta tables
# MAGIC
# MAGIC Same question the app's own "Try it" routing table uses to verify the router picks Genie.

# COMMAND ----------
from agent_server.tools.genie_tool import ask_genie_impl

result = ask_genie_impl("What is the total open PO quantity by supplier for Q4 2026?")
if result.error:
    print(f"Genie call failed: {result.error}")
else:
    print(f"Answer: {result.answer}\n")
    print(f"Generated SQL:\n{result.sql}\n")
    print(f"Rows: {result.rows}")

# COMMAND ----------
# MAGIC %md
# MAGIC Genie wrote and ran that SQL against the same five Delta tables from notebook 01, and returns
# MAGIC `.sql` for the same traceability discipline as the operational agent.
# MAGIC
# MAGIC ### Follow-up, same Genie conversation
# MAGIC Genie has its own multi-turn conversation API — separate from the LangGraph checkpointer
# MAGIC you'll see in notebook 03.

# COMMAND ----------
if not result.error:
    followup = ask_genie_impl(
        "And which of those suppliers is currently flagged at_risk?",
        conversation_id=result.conversation_id,
    )
    print(f"Answer: {followup.answer}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Vector Search — hybrid retrieval over the knowledge corpus
# MAGIC
# MAGIC The knowledge corpus is ~70 vendored PDFs (contracts, supplier notifications, competitor
# MAGIC catalogs, promotion briefs, market events) — the same fictional company world as the
# MAGIC operational data, so all three agents describe one consistent story.

# COMMAND ----------
from agent_server.tools.knowledge_tool import query_knowledge_impl

try:
    knowledge = query_knowledge_impl("What do our Caterpillar contracts say about late-delivery penalties?")
    for p in knowledge.passages:
        print(f"[{p.doc_type.value}] {p.source} (score={p.score})")
        print(f"  {p.content[:200]}...")
except RuntimeError as exc:
    print(f"Vector Search call failed: {exc}")

# COMMAND ----------
# MAGIC %md
# MAGIC `query_knowledge_impl` defaults to `HYBRID` search (vector + keyword) — better recall on exact
# MAGIC identifiers like contract IDs and SKUs than pure semantic search alone. Pass
# MAGIC `doc_types=[DocType.CONTRACT]` to narrow by document type, or `ann_only=True` to force pure
# MAGIC vector search for comparison.
# MAGIC
# MAGIC Next up: **`03_agent_end_to_end.py`** — watch the supervisor call all three of these engines
# MAGIC together, plus memory and human approval.
