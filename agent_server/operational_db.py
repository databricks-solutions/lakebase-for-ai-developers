"""Runtime data access for the Operational agent — owned by the app (ships in the app wheel).

The `data/` package is dev/setup tooling and is NOT packaged into the App, so the operational
tool can't import `data.operational._lakebase` at runtime. This module provides the same two
primitives the hybrid query needs — a Lakebase connection pool and a query-embedding call —
built on the supported `databricks_ai_bridge.lakebase` pool (the same OAuth-managed machinery as
the checkpointer/store), pointed at the operational schema rather than the memory schema.

`embed_query` + `vector_literal` mirror `data/operational/_lakebase.py` so the read path can't
drift from the write/seed path.
"""

from __future__ import annotations

from functools import lru_cache

from databricks.sdk import WorkspaceClient
from databricks_ai_bridge.lakebase import LakebasePool

from agent_server.config import settings
from agent_server.lakebase import init_lakebase_config


@lru_cache(maxsize=1)
def _ws() -> WorkspaceClient:
    return WorkspaceClient()


@lru_cache(maxsize=1)
def operational_pool() -> LakebasePool:
    """Sync Lakebase pool scoped to the operational schema (the pre-seeded pgvector
    `quality_incidents` + the synced relational tables). Sync so it runs safely inside the
    graph's sync gather node (LangGraph executes sync nodes in a worker thread)."""
    cfg = init_lakebase_config()
    return LakebasePool(
        instance_name=cfg.instance_name,
        autoscaling_endpoint=cfg.autoscaling_endpoint,
        project=cfg.autoscaling_project,
        branch=cfg.autoscaling_branch,
        schema=settings.lakebase_operational_schema,
    )


def vector_literal(vec: list[float]) -> str:
    """pgvector text form '[1,2,3]' for casting `::vector` (matches data/operational/_lakebase.py)."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def embed_query(text: str) -> list[float]:
    """Embed one query string via the Databricks embedding serving endpoint."""
    resp = _ws().serving_endpoints.query(name=settings.embedding_endpoint, input=[text])
    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
    if not data:
        raise RuntimeError(f"Unexpected embedding response from {settings.embedding_endpoint}: {resp!r}")
    item = data[0]
    emb = getattr(item, "embedding", None)
    if emb is None and isinstance(item, dict):
        emb = item.get("embedding")
    if emb is None:
        raise RuntimeError(f"No embedding in response from {settings.embedding_endpoint}: {resp!r}")
    return list(emb)


__all__ = ["operational_pool", "embed_query", "vector_literal"]
