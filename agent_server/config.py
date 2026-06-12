"""Single env-aware settings loader. Every script and module reads from here.

Per CLAUDE.md: load `.env` only when NOT running on Databricks (detect via
`DATABRICKS_RUNTIME_VERSION`, which DBR and Apps both set). On Databricks, auth and config
come from the runtime / app service principal.

Nothing in here is workspace-specific — all values are env-driven with sensible defaults so
the whole team can run against their own catalog/schema/endpoints by editing `.env`.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("genie_space_id")
    @classmethod
    def _normalize_genie_space_id(cls, v: str) -> str:
        # The DABs bundle defaults GENIE_SPACE_ID to a non-empty sentinel ("unset") because Apps
        # rejects env entries with no value and DABs drops empty strings. Treat the sentinel (and
        # any blank) as unset so the Analytics route degrades gracefully (genie_tool checks falsy).
        return "" if (v or "").strip().lower() in ("", "unset", "none") else v

    # --- LLM endpoints — two LLM callsites (router + planner).
    # CLAUDE.md anticipates per-tier sizing (fast/mid/strong) for cost; promoting to that
    # is a one-line .env change. The gather/retrieval agents use Vector Search / Genie /
    # Lakebase (not a ChatDatabricks LLM endpoint), so no retrieval endpoint is needed here.
    # Router is a constrained CLASSIFIER (pick 0-3 gather agents + one-line reason), not a
    # reasoning task — a fast small model is plenty and cuts ~3-12s off every run vs Opus. Keep
    # Opus for the planner (complex synthesis). (Validated: routing/gate scorers unchanged.)
    llm_router_endpoint: str = Field(
        default="databricks-claude-haiku-4-5", alias="LLM_ROUTER_ENDPOINT"
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
    # Long-term memory recall tuning (hydrate node). `limit` caps items per memory type. Recall is
    # already scoped (preferences/approvals by user, supplier_notes by surfaced supplier), so the
    # primary precision lever is `limit`, not a hard score cutoff. `threshold` is an OPTIONAL soft
    # floor — default 0.0 (off) because the curated `memory_text` describes the past *decision*
    # while the recall query is the new *question*, so genuinely-relevant matches score modestly
    # (~0.3–0.4 observed with gte-large), and a high cutoff silently drops them. Raise it only if a
    # large cross-user supplier-notes corpus starts injecting noise.
    memory_recall_limit: int = Field(default=3, alias="MEMORY_RECALL_LIMIT")
    memory_similarity_threshold: float = Field(
        default=0.0, alias="MEMORY_SIMILARITY_THRESHOLD"
    )
    # Short-term memory (WS1): how many prior conversation turns (messages) to feed the planner
    # as context. Trim-only for now — the full history is checkpointed per thread, but only the
    # last N are rendered into the planner prompt (older turns are dropped, not summarized yet).
    short_term_keep_recent: int = Field(default=6, alias="SHORT_TERM_KEEP_RECENT")
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
    # The app SP only gets SELECT/USAGE here (granted by the seed); the synced tables are owned by
    # the platform's `databricks_writer_*` role, not the SP.
    lakebase_operational_schema: str = Field(
        default="public", alias="LAKEBASE_OPERATIONAL_SCHEMA"
    )
    # Postgres schema for the app's OWN write-back tables (approved_actions / planning_parameters /
    # constraints — the Meridian HITL commit target). Kept SEPARATE from `lakebase_operational_schema`
    # (`public`) on purpose: the app SP must CREATE + own these, and it can only do that in a schema
    # it owns. The `postgres` app resource grants the SP CREATE-on-database, so it self-creates this
    # schema at startup (operational_db.ensure_writeback_tables) and owns everything it writes — no
    # `CREATE ON SCHEMA public` needed. Distinct from the LangGraph-owned `lakebase_memory_schema`.
    lakebase_writeback_schema: str = Field(
        default="supply_chain_planner_app", alias="LAKEBASE_WRITEBACK_SCHEMA"
    )
    # Local-dev escape hatch (Pattern 3): a full postgresql:// URL. Never commit a real one.
    lakebase_pg_url: str | None = Field(default=None, alias="LAKEBASE_PG_URL")
    # UC catalog registered for the Lakebase Postgres DB (target of Synced Tables:
    # <lakebase_uc_catalog>.<lakebase_operational_schema>.<table>). One-time `databricks postgres
    # create-catalog` per project. The DLT pipeline metadata uses a regular UC catalog (uc_catalog).
    lakebase_uc_catalog: str | None = Field(default=None, alias="LAKEBASE_UC_CATALOG")
    # Scheduling policy for the *live* operational synced tables (`inventory_current`, `open_pos`)
    # in 03_sync_to_lakebase.py. SNAPSHOT (default) is a one-time copy that goes idle — cheap, and
    # fine for the static seeded demo data. Flip to CONTINUOUS (a always-on DLT pipeline that
    # streams CDF) only when a demo needs to show live updates. The dim tables are always SNAPSHOT.
    lakebase_sync_mode: str = Field(default="SNAPSHOT", alias="LAKEBASE_SYNC_MODE")

    @field_validator("lakebase_sync_mode")
    @classmethod
    def _validate_sync_mode(cls, v: str) -> str:
        mode = (v or "SNAPSHOT").strip().upper()
        if mode not in {"SNAPSHOT", "CONTINUOUS"}:
            raise ValueError("LAKEBASE_SYNC_MODE must be SNAPSHOT or CONTINUOUS")
        return mode

    # --- MLflow ---
    mlflow_experiment_id: str | None = Field(default=None, alias="MLFLOW_EXPERIMENT_ID")
    # MLflow 3 Unity-Catalog tracing (so traces land from a Databricks App, where the default
    # artifact-storage export is blocked by egress). Bind the experiment to this UC schema; the
    # SQL warehouse backs trace storage. Catalog defaults to uc_catalog; warehouse to warehouse_id.
    mlflow_trace_catalog: str | None = Field(default=None, alias="MLFLOW_TRACE_CATALOG")
    mlflow_trace_schema: str | None = Field(default=None, alias="MLFLOW_TRACE_SCHEMA")
    mlflow_trace_table_prefix: str = Field(default="scp", alias="MLFLOW_TRACE_TABLE_PREFIX")
    mlflow_tracing_warehouse_id: str | None = Field(
        default=None, alias="MLFLOW_TRACING_SQL_WAREHOUSE_ID"
    )

    @field_validator("mlflow_tracing_warehouse_id")
    @classmethod
    def _normalize_warehouse(cls, v: str | None) -> str | None:
        # The bundle defaults this to the "unset" sentinel (DABs drops empty env values, which Apps
        # rejects). Treat the sentinel / blank as "no warehouse" → UC tracing stays off.
        return None if (v or "").strip().lower() in ("", "unset", "none") else v

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


def _job_param_overrides() -> dict[str, str]:
    """Config the setup_and_seed DABs job passes to its tasks. Serverless job tasks can't take
    environment variables, so the bundle passes catalog/Lakebase/etc. coordinates as task
    parameters instead: Python tasks as `ALIAS=value` argv pairs, notebook tasks as job widgets
    of the same names. This is the ONLY way the seed scripts (which read config solely through
    this module) learn which catalog/Lakebase to target.

    Best-effort and additive: results are merged UNDER real env (env wins), so the App — which
    sets every value as an env var — never sees these. Any failure yields no overrides.
    """
    aliases = {f.alias for f in Settings.model_fields.values() if f.alias}
    out: dict[str, str] = {}
    # (a) Python tasks: `ALIAS=value` argv pairs (spark_python_task `parameters`).
    for arg in sys.argv[1:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            if k in aliases and v:
                out[k] = v
    # (b) Notebook tasks: job widgets named by alias (notebook_task `base_parameters`).
    try:
        from databricks.sdk.runtime import dbutils  # present only on Databricks
        for k in aliases:
            try:
                v = dbutils.widgets.get(k)
                if v:
                    out[k] = v
            except Exception:
                pass
    except Exception:
        pass
    return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv_if_local()
    # Build kwargs from env, preferring alias names (matches .env.example).
    merged = {field.alias or name: os.environ[field.alias or name]
              for name, field in Settings.model_fields.items()
              if (field.alias or name) in os.environ}
    # Fill any gaps from setup_and_seed job parameters (env always wins → no effect in the App).
    for k, v in _job_param_overrides().items():
        merged.setdefault(k, v)
    return Settings.model_validate(merged)


# Module-level shortcut. Callers do: `from agent_server.config import settings`.
# NOTE: Spark lives in the data layer (`data/_spark.py`), NOT here — the agent app never uses
# Spark, and this module ships inside the app package.
settings = get_settings()
