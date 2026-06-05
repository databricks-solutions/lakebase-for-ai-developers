"""Knowledge agent tool — Vector Search over the bronze PDFs (contracts, supplier
notifications, competitor catalogs, promotion briefs, market events).

Wraps `DatabricksVectorSearch` from `databricks-langchain` and exposes a LangGraph @tool.
Supports an optional `doc_types` filter so the supervisor can scope retrieval (e.g.
"contracts only").

Both `query_knowledge` (the @tool) and `KnowledgeAgent.query()` (the typed API for the
graph's gather node) are provided.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.tools import tool

from agent_server.config import settings
from agent_server.contracts import DocType, KnowledgePassage, KnowledgeResult


@lru_cache(maxsize=1)
def _vector_store():
    """Lazy + cached. Builds the DatabricksVectorSearch client on first use so importing
    this module doesn't require workspace creds."""
    if not settings.vector_search_index:
        raise RuntimeError(
            "VECTOR_SEARCH_INDEX is not set in env. Run data/knowledge/03_build_vs_index.py "
            "then paste the index name into .env."
        )
    from databricks_langchain import DatabricksVectorSearch

    return DatabricksVectorSearch(
        index_name=settings.vector_search_index,
        # Columns we want back on each hit — drives KnowledgePassage construction.
        columns=[
            "chunk_id", "source", "filename", "doc_type", "doc_id",
            "page", "content", "customer", "supplier", "categories",
        ],
    )


def _doc_filter(doc_types: list[DocType] | None) -> dict | None:
    """Translate a DocType list into VS filter dict. None → no filter."""
    if not doc_types:
        return None
    if len(doc_types) == 1:
        return {"doc_type": doc_types[0].value}
    return {"doc_type": [dt.value for dt in doc_types]}


def _to_passage(doc) -> KnowledgePassage:
    """Convert a langchain Document into our KnowledgePassage contract."""
    m = doc.metadata or {}
    return KnowledgePassage(
        chunk_id=m.get("chunk_id", ""),
        source=m.get("source", m.get("filename", "")),
        page=m.get("page"),
        doc_type=DocType(m.get("doc_type", "contract")),
        doc_id=m.get("doc_id"),
        content=doc.page_content,
        score=m.get("score"),
    )


def query_knowledge_impl(
    query: str,
    doc_types: list[DocType] | None = None,
    k: int = 5,
) -> KnowledgeResult:
    """Typed entry point — used by the graph's gather node and by tests."""
    store = _vector_store()
    docs = store.similarity_search(query=query, k=k, filter=_doc_filter(doc_types))
    return KnowledgeResult(query=query, passages=[_to_passage(d) for d in docs])


@tool
def query_knowledge(query: str, doc_types: list[str] | None = None) -> dict:
    """Retrieve passages from the supply-chain knowledge corpus (contracts, supplier
    notifications, competitor catalogs, promotion briefs, market events).

    Use this for natural-language questions about specific documents, supplier
    announcements, contract terms, or market events. Returns the top-5 most relevant
    passages with source attribution.

    Args:
        query: The natural-language question or search phrase.
        doc_types: Optional filter — one or more of: 'contract', 'supplier_notification',
                   'competitor_catalog', 'promotion_brief', 'market_event'. Omit to search
                   the whole corpus.

    Returns:
        A dict with the query echoed back and a list of passages, each containing
        chunk_id, source, doc_type, doc_id, page, content, and (when available) a score.
    """
    parsed_types = [DocType(dt) for dt in doc_types] if doc_types else None
    return query_knowledge_impl(query, parsed_types).model_dump()
