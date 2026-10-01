"""AI Search (Databricks Vector Search) tool over the supply-chain knowledge corpus.

Queries the Delta-Sync index built by the repo's data/knowledge pipeline (contracts,
supplier notifications, competitor catalogs, promotion briefs, market events). Uses the
Databricks SDK's query_index so it authenticates with the app service principal's ambient
credentials on Databricks Apps (no PAT needed).
"""

from __future__ import annotations

import logging
import os

from databricks.sdk import WorkspaceClient
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Supply-chain AI Search (Vector Search) index built by the data/knowledge pipeline on fevm-sfefsp.
# Concrete to this supply-chain demo; env override available if the index ever moves.
_INDEX = os.environ.get(
    "VECTOR_SEARCH_INDEX",
    "serverless_stable_sfefsp_catalog.supply_chain.knowledge_chunks_index",
)
_COLUMNS = ["doc_type", "doc_id", "filename", "page", "content", "customer", "supplier"]


@tool
def search_knowledge(query: str, num_results: int = 5) -> list[dict]:
    """Search the supply-chain knowledge corpus via Databricks AI Search (Vector Search).

    The corpus is contracts, supplier notifications, competitor catalogs, promotion briefs,
    and market-event notes. Use this for questions about contract terms, penalties, supplier
    price changes, promotions, or market events. Returns the most relevant passages with
    their source metadata.

    Args:
        query: Natural-language question or search phrase.
        num_results: How many passages to return (default 5).
    """
    w = WorkspaceClient()
    for query_type in ("HYBRID", "ANN"):
        try:
            resp = w.vector_search_indexes.query_index(
                index_name=_INDEX,
                columns=_COLUMNS,
                query_text=query,
                num_results=num_results,
                query_type=query_type,
            )
            cols = (
                [c.name for c in resp.manifest.columns]
                if resp.manifest and resp.manifest.columns
                else _COLUMNS + ["score"]
            )
            rows = resp.result.data_array if resp.result and resp.result.data_array else []
            return [dict(zip(cols, r)) for r in rows]
        except Exception as exc:
            logger.warning("AI Search query_type=%s failed: %s", query_type, exc)
    return []
