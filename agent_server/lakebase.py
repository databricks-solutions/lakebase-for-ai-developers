"""Lakebase wiring for the agent: short-term checkpointer + long-term store.

Ported from the reference template (`agent-langgraph-advanced/agent_server/utils_memory.py`)
but reads from `agent_server.config.settings` so there is one config source of truth (CLAUDE.md).

- `AsyncCheckpointSaver` — thread/session checkpoints; HITL `interrupt()` resumes from here.
- `AsyncDatabricksStore` — Lakebase-backed Postgres store with embeddings (the pgvector path);
  P0 uses it write-back-only at commit (persist the verdict, skip hydrate-and-use).

Connection priority mirrors the library's mutually-exclusive modes:
  autoscaling_endpoint  >  autoscaling project+branch  >  provisioned instance_name.

The DB OAuth credentials are short-lived and rotated by the library via the Databricks SDK —
no Postgres password lives in config or `.env`.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional, Tuple

from databricks.sdk import WorkspaceClient
from databricks_langchain import AsyncCheckpointSaver, AsyncDatabricksStore
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from agent_server import contracts
from agent_server.config import settings

logger = logging.getLogger(__name__)

# Contract Pydantic types/enums that land in the checkpointed AgentState. LangGraph's msgpack serde
# is permissive today but warns ("Deserializing unregistered type …") and will block unregistered
# types under a future LANGGRAPH_STRICT_MSGPACK default. We register them explicitly so resume keeps
# working forward-compatibly and the warning goes away. databricks_langchain.AsyncCheckpointSaver
# does not forward a `serde` kwarg, so we set checkpointer.serde after construction.
_CHECKPOINT_CONTRACT_TYPES = (
    contracts.RouterDecision,
    contracts.KnowledgeResult,
    contracts.KnowledgePassage,
    contracts.GenieResult,
    contracts.OperationalResult,
    contracts.OperationalRow,
    contracts.PlannerRecommendation,
    contracts.HITLDecision,
    contracts.HITLVerdict,
    contracts.DocType,
)


def _contract_aware_serde() -> JsonPlusSerializer:
    """A JsonPlusSerializer that explicitly allows the contract types in the checkpoint."""
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            (t.__module__, t.__name__) for t in _CHECKPOINT_CONTRACT_TYPES
        ]
    )

# Long-lived resources opened once at app startup (start_server.py lifespan) and reused
# across requests. Falls back to a per-call context when unset (e.g. eval / smoke scripts).
_lakebase_resources: Optional[Tuple[AsyncCheckpointSaver, AsyncDatabricksStore]] = None


def set_lakebase_resources(
    checkpointer: AsyncCheckpointSaver, store: AsyncDatabricksStore
) -> None:
    global _lakebase_resources
    _lakebase_resources = (checkpointer, store)


@dataclass(frozen=True)
class LakebaseConfig:
    instance_name: Optional[str]
    autoscaling_endpoint: Optional[str]
    autoscaling_project: Optional[str]
    autoscaling_branch: Optional[str]
    embedding_endpoint: str
    embedding_dims: int = 1024
    memory_schema: Optional[str] = None

    @property
    def description(self) -> str:
        return (
            self.autoscaling_endpoint
            or self.instance_name
            or f"{self.autoscaling_project}/{self.autoscaling_branch}"
        )


def init_lakebase_config() -> LakebaseConfig:
    """Build the connection config from `settings`, applying the library's priority rules."""
    endpoint = settings.lakebase_autoscaling_endpoint or None
    raw_name = settings.lakebase_instance_name or None
    project = settings.lakebase_autoscaling_project or None
    branch = settings.lakebase_autoscaling_branch or None

    has_autoscaling = bool(project and branch)
    if not endpoint and not raw_name and not has_autoscaling:
        raise ValueError(
            "Lakebase configuration is required but not set. Set one of:\n"
            "  Option 1 (autoscaling endpoint): LAKEBASE_AUTOSCALING_ENDPOINT=<endpoint>\n"
            "  Option 2 (autoscaling): LAKEBASE_AUTOSCALING_PROJECT + LAKEBASE_AUTOSCALING_BRANCH\n"
            "  Option 3 (provisioned): LAKEBASE_INSTANCE_NAME=<instance>\n"
        )

    # Mutually exclusive: endpoint > project+branch > instance_name.
    if endpoint:
        # The library resolves an endpoint via the full resource path
        # `projects/<p>/branches/<b>/endpoints/<id>`. Accept either a complete path or a bare
        # endpoint id combined with project+branch (same convention as data/operational/_lakebase.py).
        if not endpoint.startswith("projects/"):
            if not has_autoscaling:
                raise ValueError(
                    "LAKEBASE_AUTOSCALING_ENDPOINT is a bare endpoint id, so also set "
                    "LAKEBASE_AUTOSCALING_PROJECT and LAKEBASE_AUTOSCALING_BRANCH (or give the "
                    "full projects/<p>/branches/<b>/endpoints/<id> path)."
                )
            endpoint = f"projects/{project}/branches/{branch}/endpoints/{endpoint}"
        instance_name = project = branch = None
    elif has_autoscaling:
        instance_name = endpoint = None
    else:
        instance_name = resolve_lakebase_instance_name(raw_name)
        endpoint = project = branch = None

    return LakebaseConfig(
        instance_name=instance_name,
        autoscaling_endpoint=endpoint,
        autoscaling_project=project,
        autoscaling_branch=branch,
        embedding_endpoint=settings.embedding_endpoint,
        memory_schema=settings.lakebase_memory_schema or None,
    )


def _is_lakebase_hostname(value: str) -> bool:
    """Hostname pattern: instance-{uuid}.database.{env}.cloud.databricks.com."""
    return ".database." in value and value.endswith(".com")


def resolve_lakebase_instance_name(
    instance_name: str, workspace_client: Optional[WorkspaceClient] = None
) -> str:
    """Resolve a Lakebase instance name from a hostname (Apps `value_from` gives a hostname)."""
    if not _is_lakebase_hostname(instance_name):
        return instance_name

    client = workspace_client or WorkspaceClient()
    hostname = instance_name
    try:
        instances = list(client.database.list_database_instances())
    except Exception as exc:
        raise ValueError(
            f"Unable to list database instances to resolve hostname '{hostname}'. "
            "Ensure you have access to database instances."
        ) from exc

    for instance in instances:
        rw_dns = getattr(instance, "read_write_dns", None)
        ro_dns = getattr(instance, "read_only_dns", None)
        if hostname in (rw_dns, ro_dns):
            resolved = getattr(instance, "name", None)
            if not resolved:
                raise ValueError(
                    f"Found instance for hostname '{hostname}' but its name is unavailable."
                )
            logger.info("Resolved Lakebase hostname '%s' to instance '%s'", hostname, resolved)
            return resolved

    raise ValueError(
        f"Unable to find a database instance matching hostname '{hostname}'."
    )


def _is_databricks_app_env() -> bool:
    return bool(os.getenv("DATABRICKS_APP_NAME"))


def get_lakebase_access_error_message(target: str) -> str:
    """Context-aware troubleshooting text for Lakebase connect failures."""
    if _is_databricks_app_env():
        app_name = os.getenv("DATABRICKS_APP_NAME")
        return (
            f"Failed to connect to Lakebase '{target}'. The App Service Principal for "
            f"'{app_name}' may lack access.\n"
            "Fix: app → Edit → App resources → Add resource → add the Lakebase instance, "
            "then grant it CAN_CONNECT_AND_CREATE."
        )
    return (
        f"Failed to connect to Lakebase '{target}'. Verify: (1) the instance/endpoint name is "
        "correct, (2) you have permission, (3) your Databricks auth (profile) is configured."
    )


@asynccontextmanager
async def lakebase_context(config: LakebaseConfig):
    """Open (checkpointer, store) for short-term + long-term memory."""
    async with AsyncCheckpointSaver(
        instance_name=config.instance_name,
        autoscaling_endpoint=config.autoscaling_endpoint,
        project=config.autoscaling_project,
        branch=config.autoscaling_branch,
        schema=config.memory_schema,
    ) as checkpointer, AsyncDatabricksStore(
        instance_name=config.instance_name,
        autoscaling_endpoint=config.autoscaling_endpoint,
        project=config.autoscaling_project,
        branch=config.autoscaling_branch,
        embedding_endpoint=config.embedding_endpoint,
        embedding_dims=config.embedding_dims,
        schema=config.memory_schema,
    ) as store:
        # Register the contract types so resume is forward-compatible (see _contract_aware_serde).
        checkpointer.serde = _contract_aware_serde()
        yield checkpointer, store


@asynccontextmanager
async def acquire_lakebase_resources(config: LakebaseConfig):
    """Yield long-lived resources opened at startup, or fall back to a fresh per-call context."""
    if _lakebase_resources is not None:
        yield _lakebase_resources
    else:
        async with lakebase_context(config) as resources:
            yield resources


__all__ = [
    "LakebaseConfig",
    "init_lakebase_config",
    "lakebase_context",
    "acquire_lakebase_resources",
    "set_lakebase_resources",
    "get_lakebase_access_error_message",
    "resolve_lakebase_instance_name",
]
