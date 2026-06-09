"""Knowledge agent tool — Vector Search over the bronze PDFs (contracts, supplier
notifications, competitor catalogs, promotion briefs, market events).

Wraps `DatabricksVectorSearch` from `databricks-langchain` and exposes a LangGraph @tool.

**Hybrid search by default.** Our corpus contains exact-match identifiers — contract IDs
(`CTR-2024-1000`), SKU codes (`SKU-1001`), supplier names (`Henkel`, `Nucor`) — where pure ANN
can miss term-specific hits. Hybrid (vector + BM25) gives both signals; set `ann_only=True` to
force semantic-only when you specifically don't want keyword boost.

Supports a `doc_types` filter so the supervisor can scope retrieval (e.g. "contracts only").
"""

from __future__ import annotations

import logging
import os

from langchain_core.tools import tool

from agent_server.config import settings
from agent_server.contracts import DocType, KnowledgePassage, KnowledgeResult

logger = logging.getLogger(__name__)


def _export_local_vs_credentials() -> None:
    """Local U2M wrinkle: `DatabricksVectorSearch` → `VectorSearchClient` requires PAT or SP
    creds and does NOT honor OAuth-profile auth. Resolve a bearer token from the profile and
    expose it via DATABRICKS_HOST/DATABRICKS_TOKEN so the underlying client authenticates.
    No-op on Databricks (ambient SP auth) and when no profile is set or a token is already
    present. Same pattern `agent_server/agent.py` uses for MLflow.
    NOTE: U2M tokens expire (~1h); fine for a local dev session, deployed App is unaffected."""
    if settings.on_databricks or not settings.databricks_profile or os.getenv("DATABRICKS_TOKEN"):
        return
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        token = w.config.authenticate().get("Authorization", "").replace("Bearer ", "")
        if token:
            os.environ["DATABRICKS_HOST"] = w.config.host
            os.environ["DATABRICKS_TOKEN"] = token
    except Exception as exc:
        logger.warning("Could not export local VS credentials: %s", exc)


def _vector_store():
    """Build the DatabricksVectorSearch client for THIS request.

    OBO-first (minimal-permission): when the caller's forwarded token is present we query the
    index AS THE USER, so Unity Catalog governs per user and the app SP needs no grant on the
    index. NOT cached — caching would reuse one user's token for everyone. Falls back to a local
    U2M token, then the app SP's OAuth creds (eval / background tasks)."""
    if not settings.vector_search_index:
        raise RuntimeError(
            "VECTOR_SEARCH_INDEX is not set in env. Run data/knowledge/03_build_vs_index.py "
            "then paste the index name into .env."
        )
    from databricks_langchain import DatabricksVectorSearch

    from agent_server.obo import get_obo_token, workspace_host

    # VectorSearchClient (under DatabricksVectorSearch) requires an explicit token / SP creds —
    # it does NOT use the ambient OAuth chain. Prefer the caller's OBO token.
    client_args: dict = {"disable_notice": True}  # suppress the PAT-recommendation banner
    obo = get_obo_token()
    if obo:
        # On-behalf-of-user: UC enforces the caller's own access to the corpus.
        if (host := workspace_host()):
            client_args["workspace_url"] = host
        client_args["personal_access_token"] = obo
    else:
        _export_local_vs_credentials()
        if os.environ.get("DATABRICKS_HOST") and os.environ.get("DATABRICKS_TOKEN"):
            # Local U2M: token exported above.
            client_args["workspace_url"] = os.environ["DATABRICKS_HOST"]
            client_args["personal_access_token"] = os.environ["DATABRICKS_TOKEN"]
        elif os.environ.get("DATABRICKS_CLIENT_ID") and os.environ.get("DATABRICKS_CLIENT_SECRET"):
            # App SP fallback (no forwarded user token — e.g. a background/eval run).
            if (host := workspace_host()):
                client_args["workspace_url"] = host
            client_args["service_principal_client_id"] = os.environ["DATABRICKS_CLIENT_ID"]
            client_args["service_principal_client_secret"] = os.environ["DATABRICKS_CLIENT_SECRET"]

    return DatabricksVectorSearch(
        index_name=settings.vector_search_index,
        columns=[
            "chunk_id", "source", "filename", "doc_type", "doc_id",
            "page", "content", "customer", "supplier", "categories",
        ],
        client_args=client_args,
        include_score=True,  # surfaces relevance score on doc.metadata
    )


def _doc_filter(doc_types: list[DocType] | None) -> dict | None:
    """Translate a DocType list into VS filter dict. None → no filter."""
    if not doc_types:
        return None
    if len(doc_types) == 1:
        return {"doc_type": doc_types[0].value}
    return {"doc_type": [dt.value for dt in doc_types]}


def _to_passage(doc) -> KnowledgePassage:
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
    ann_only: bool = False,
) -> KnowledgeResult:
    """Typed entry point — used by the graph's gather node and tests.

    `ann_only=True` forces pure semantic search; default is HYBRID (vector + BM25)
    for better recall on exact-match identifiers (contract IDs, SKUs, supplier names)
    common in our corpus."""
    store = _vector_store()
    docs = store.similarity_search(
        query=query,
        k=k,
        filter=_doc_filter(doc_types),
        query_type="ANN" if ann_only else "HYBRID",
    )
    return KnowledgeResult(query=query, passages=[_to_passage(d) for d in docs])


@tool
def query_knowledge(
    query: str,
    doc_types: list[str] | None = None,
    ann_only: bool = False,
) -> dict:
    """Retrieve passages from the supply-chain knowledge corpus (contracts, supplier
    notifications, competitor catalogs, promotion briefs, market events).

    Hybrid search (vector + keyword) by default — best for questions mentioning specific
    identifiers (contract IDs like 'CTR-2024-1000', SKU codes, supplier names). Set
    `ann_only=True` for pure semantic search.

    Args:
        query: The natural-language question or search phrase.
        doc_types: Optional filter — one or more of 'contract', 'supplier_notification',
                   'competitor_catalog', 'promotion_brief', 'market_event'. Omit for whole corpus.
        ann_only: When True, use semantic-only search (ANN). Default False uses hybrid.

    Returns:
        A dict with the query echoed back and a list of passages (chunk_id, source,
        doc_type, doc_id, page, content, score).
    """
    parsed_types = [DocType(dt) for dt in doc_types] if doc_types else None
    return query_knowledge_impl(query, parsed_types, ann_only=ann_only).model_dump()
