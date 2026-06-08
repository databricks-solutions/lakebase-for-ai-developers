"""Build the Vector Search Delta-Sync index over `knowledge_chunks`.

Uses the Databricks SDK directly (`w.vector_search_*`) — the `databricks-vectorsearch`
client doesn't honor OAuth U2M profiles, so going through the SDK keeps auth uniform with
the rest of the agent.

Idempotent. Endpoint creation, if needed, completes asynchronously (`~10 min` typical).
Index creation triggers a sync; we wait until the index reports a terminal state.

Runs locally (`uv run python data/knowledge/03_build_vs_index.py`) or on Databricks.
Prints the endpoint + index names to paste into `.env`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    REPO_ROOT = str(Path(__file__).resolve().parents[2])
except NameError:
    REPO_ROOT = str(Path.cwd().resolve())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.vectorsearch import (
    DeltaSyncVectorIndexSpecRequest,
    EmbeddingSourceColumn,
    EndpointType,
    PipelineType,
    VectorIndexType,
)

from agent_server.config import settings


COLUMNS_TO_SYNC = [
    "chunk_id", "source", "filename", "doc_type", "doc_id",
    "page", "content", "customer", "supplier", "categories",
]


def _resolve_names() -> tuple[str, str, str]:
    endpoint = settings.vector_search_endpoint or f"{settings.uc_catalog}-vs-endpoint"
    index = settings.vector_search_index or settings.default_index_name
    source = settings.chunks_table
    return endpoint, index, source


def _ensure_endpoint(w: WorkspaceClient, name: str) -> None:
    existing = {e.name for e in w.vector_search_endpoints.list_endpoints()}
    if name in existing:
        print(f"  endpoint {name!r} exists")
        return
    print(f"  creating endpoint {name!r} (STANDARD) — async, ~10 min...")
    w.vector_search_endpoints.create_endpoint(name=name, endpoint_type=EndpointType.STANDARD)
    while True:
        ep = next((e for e in w.vector_search_endpoints.list_endpoints() if e.name == name), None)
        state = getattr(getattr(ep, "endpoint_status", None), "state", None)
        print(f"    endpoint state: {state}")
        if state and "ONLINE" in str(state):
            return
        time.sleep(20)


def _ensure_index(w: WorkspaceClient, endpoint: str, index: str, source: str) -> None:
    existing = {i.name for i in w.vector_search_indexes.list_indexes(endpoint_name=endpoint)}
    if index in existing:
        print(f"  index {index!r} exists — triggering sync")
        w.vector_search_indexes.sync_index(index_name=index)
        return
    print(f"  creating index {index!r}…")
    w.vector_search_indexes.create_index(
        name=index,
        endpoint_name=endpoint,
        primary_key="chunk_id",
        index_type=VectorIndexType.DELTA_SYNC,
        delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
            source_table=source,
            pipeline_type=PipelineType.TRIGGERED,
            columns_to_sync=COLUMNS_TO_SYNC,
            embedding_source_columns=[
                EmbeddingSourceColumn(
                    name="content",
                    embedding_model_endpoint_name=settings.embedding_endpoint,
                )
            ],
        ),
    )


def _wait_until_online(w: WorkspaceClient, index: str) -> None:
    print("\n  waiting for index to come online…")
    while True:
        idx = w.vector_search_indexes.get_index(index_name=index)
        state = getattr(getattr(idx, "status", None), "detailed_state", None)
        print(f"    index state: {state}")
        if state and "ONLINE" in str(state):
            return
        time.sleep(30)


def main() -> None:
    endpoint, index, source = _resolve_names()
    print(f"Endpoint     : {endpoint}")
    print(f"Index        : {index}")
    print(f"Source table : {source}")
    print(f"Embedding    : {settings.embedding_endpoint}")

    w = WorkspaceClient()
    _ensure_endpoint(w, endpoint)
    _ensure_index(w, endpoint, index, source)
    _wait_until_online(w, index)

    print(f"\n✓ Index ready: {index}")
    print(f"\nAdd to .env if not already set:")
    print(f"  VECTOR_SEARCH_ENDPOINT={endpoint}")
    print(f"  VECTOR_SEARCH_INDEX={index}")


if __name__ == "__main__":
    main()
