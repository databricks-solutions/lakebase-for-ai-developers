"""Single env-aware settings loader. Every script and module reads from here.

Per CLAUDE.md: load `.env` only when NOT running on Databricks (detect via
`DATABRICKS_RUNTIME_VERSION`, which DBR and Apps both set). On Databricks, auth and config
come from the runtime / app service principal.

Nothing in here is workspace-specific — all values are env-driven with sensible defaults so
the whole team can run against their own catalog/schema/endpoints by editing `.env`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


def _on_databricks() -> bool:
    return bool(os.environ.get("DATABRICKS_RUNTIME_VERSION"))


def _load_dotenv_if_local() -> None:
    if _on_databricks():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Walk up from CWD to find the nearest .env (so scripts work from subdirs).
    here = Path.cwd()
    for parent in [here, *here.parents]:
        candidate = parent / ".env"
        if candidate.exists():
            load_dotenv(candidate)
            return


class Settings(BaseModel):
    """All runtime config in one place. Resolved from env at import time."""

    # --- Auth (local only; ignored on Databricks) ---
    databricks_profile: str | None = Field(default=None, alias="DATABRICKS_CONFIG_PROFILE")

    # --- Unity Catalog ---
    uc_catalog: str = Field(default="supply_chain", alias="UC_CATALOG")
    uc_schema: str = Field(default="planner", alias="UC_SCHEMA")
    uc_volume: str = Field(default="documents", alias="UC_VOLUME")

    # --- Knowledge agent (Vector Search) ---
    vector_search_endpoint: str = Field(default="", alias="VECTOR_SEARCH_ENDPOINT")
    vector_search_index: str = Field(default="", alias="VECTOR_SEARCH_INDEX")
    embedding_endpoint: str = Field(
        default="databricks-gte-large-en", alias="DATABRICKS_EMBEDDING_ENDPOINT"
    )

    # --- Analytics agent (Genie) ---
    genie_space_id: str = Field(default="", alias="GENIE_SPACE_ID")
    warehouse_id: str | None = Field(default=None, alias="DATABRICKS_WAREHOUSE_ID")

    # --- LLM endpoints — single inference model for v1 (one model, three callsites).
    # CLAUDE.md anticipates per-tier sizing (fast/mid/strong) for cost; promoting to that
    # is a one-line .env change. For now everything points at the same Opus endpoint.
    llm_router_endpoint: str = Field(
        default="databricks-claude-opus-4-8", alias="LLM_ROUTER_ENDPOINT"
    )
    llm_retrieval_endpoint: str = Field(
        default="databricks-claude-opus-4-8", alias="LLM_RETRIEVAL_ENDPOINT"
    )
    llm_planner_endpoint: str = Field(
        default="databricks-claude-opus-4-8", alias="LLM_PLANNER_ENDPOINT"
    )

    # --- Knowledge ingestion (local-only paths used by data/knowledge/01_upload_pdfs.py) ---
    seed_data_path: str = Field(
        default="../strategic_revenue_demo/seed_data/bronze_documents",
        alias="SEED_DATA_PATH",
    )

    # --- Lakebase (WS1 wires these later) ---
    lakebase_instance_name: str | None = Field(default=None, alias="LAKEBASE_INSTANCE_NAME")
    lakebase_database: str = Field(
        default="databricks_postgres", alias="LAKEBASE_DATABASE"
    )
    lakebase_memory_schema: str = Field(
        default="supply_chain_planner_memory", alias="LAKEBASE_AGENT_MEMORY_SCHEMA"
    )

    # --- MLflow ---
    mlflow_experiment_id: str | None = Field(default=None, alias="MLFLOW_EXPERIMENT_ID")

    # --- Derived helpers ---
    @property
    def volume_uri(self) -> str:
        return f"/Volumes/{self.uc_catalog}/{self.uc_schema}/{self.uc_volume}"

    @property
    def chunks_table(self) -> str:
        return f"{self.uc_catalog}.{self.uc_schema}.knowledge_chunks"

    @property
    def default_index_name(self) -> str:
        return f"{self.uc_catalog}.{self.uc_schema}.knowledge_chunks_index"

    @property
    def on_databricks(self) -> bool:
        return _on_databricks()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv_if_local()
    # Build kwargs from env, preferring alias names (matches .env.example).
    return Settings.model_validate(
        {field.alias or name: os.environ[field.alias or name]
         for name, field in Settings.model_fields.items()
         if (field.alias or name) in os.environ}
    )


# Module-level shortcut. Callers do: `from agent_server.config import settings`.
settings = get_settings()
