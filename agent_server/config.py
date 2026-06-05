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

    # --- Demo (operational data) ---
    # In-scope planner identity for the user_access ACL. Unset → the current Databricks user
    # (so the OBO demo works for whoever runs it); set to a fixed email for a shared demo.
    demo_planner_user: str | None = Field(default=None, alias="DEMO_PLANNER_USER")

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

    # --- Knowledge ingestion (paths used by data/knowledge/01_upload_pdfs.py) ---
    # Vendored in-repo (committed); resolved relative to the repo root by run-from-root scripts.
    seed_data_path: str = Field(
        default="data/knowledge/bronze_documents",
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
    # Autoscaling ("projects") connection form — set instead of instance-name for autoscaling.
    # _lakebase.connect() (02/04) accepts EITHER a full resource path here
    # (projects/<p>/branches/<b>/endpoints/<id>) OR a bare endpoint id that it combines with the
    # project/branch below — the same pair 03_sync_to_lakebase.py uses — so one config serves all.
    lakebase_autoscaling_endpoint: str | None = Field(
        default=None, alias="LAKEBASE_AUTOSCALING_ENDPOINT"
    )
    lakebase_autoscaling_project: str | None = Field(
        default=None, alias="LAKEBASE_AUTOSCALING_PROJECT"
    )
    lakebase_autoscaling_branch: str | None = Field(
        default=None, alias="LAKEBASE_AUTOSCALING_BRANCH"
    )
    # Postgres schema holding the operational tables (the pre-seeded pgvector `quality_incidents`
    # plus the synced relational tables) — kept together so the hybrid query joins without
    # cross-schema qualification. Distinct from `lakebase_memory_schema` (LangGraph-owned).
    lakebase_operational_schema: str = Field(
        default="public", alias="LAKEBASE_OPERATIONAL_SCHEMA"
    )
    # Local-dev escape hatch (Pattern 3): a full postgresql:// URL. Never commit a real one.
    lakebase_pg_url: str | None = Field(default=None, alias="LAKEBASE_PG_URL")
    # UC catalog registered for the Lakebase Postgres DB (target of Synced Tables:
    # <lakebase_uc_catalog>.<lakebase_operational_schema>.<table>). One-time `databricks postgres
    # create-catalog` per project. The DLT pipeline metadata uses a regular UC catalog (uc_catalog).
    lakebase_uc_catalog: str | None = Field(default=None, alias="LAKEBASE_UC_CATALOG")

    # --- MLflow ---
    mlflow_experiment_id: str | None = Field(default=None, alias="MLFLOW_EXPERIMENT_ID")

    # --- Derived helpers ---
    @property
    def seed_data_dir(self) -> Path:
        """`seed_data_path` as an absolute path. Relative values resolve against the repo root
        (this file is `agent_server/config.py`), so it works regardless of the caller's CWD."""
        p = Path(self.seed_data_path).expanduser()
        return p if p.is_absolute() else Path(__file__).resolve().parents[1] / p

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
# NOTE: Spark lives in the data layer (`data/_spark.py`), NOT here — the agent app never uses
# Spark, and this module ships inside the app package.
settings = get_settings()
