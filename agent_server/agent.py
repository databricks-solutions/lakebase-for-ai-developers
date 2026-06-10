"""Agent server handlers: drive the supervisor StateGraph behind the MLflow agent-server API.

Unlike the reference template (a message-based ReAct `create_agent`), our agent is a custom
multi-node `StateGraph` (`agent_server.graph.build_graph`) keyed on `AgentState`. So this module
translates graph state into the OpenAI Responses stream the chat UI / API expect, rather than
streaming token chunks.

Run/poll/resume transport + the Lakebase lifespan are provided by `LongRunningAgentServer`
(see `start_server.py`). HITL: a fresh question starts a run; the run pauses at the planner's
`interrupt()` (Slice 3 — the P0 stub auto-approves for now); the client resumes by re-invoking
with the same `thread_id` and an HITL verdict in `custom_inputs`, which becomes `Command(resume=...)`.

MLflow autolog is enabled at import so the whole run is one trace.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, AsyncGenerator, Optional

import mlflow
from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
    create_text_output_item,
)

from agent_server.config import settings
from agent_server.contracts import HITLDecision, HITLVerdict
from agent_server.graph.build_graph import build_graph
from agent_server.lakebase import (
    acquire_lakebase_resources,
    get_lakebase_access_error_message,
    init_lakebase_config,
)

logger = logging.getLogger(__name__)

mlflow.langchain.autolog()
logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)


def _export_local_trace_credentials() -> None:
    """Local U2M wrinkle: MLflow's async trace exporter doesn't resolve OAuth-profile creds, so
    trace export 401s. Resolve a bearer token from the profile and expose it via
    DATABRICKS_HOST/DATABRICKS_TOKEN so the exporter authenticates. No-op on Databricks (ambient
    SP auth handles trace export) and when no profile is set or a token is already present.
    NOTE: U2M tokens expire (~1h) — fine for a local dev session; the deployed App is unaffected."""
    if settings.on_databricks or not settings.databricks_profile or os.getenv("DATABRICKS_TOKEN"):
        return
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        token = w.config.authenticate().get("Authorization", "").replace("Bearer ", "")
        if token:
            os.environ["DATABRICKS_HOST"] = w.config.host
            os.environ["DATABRICKS_TOKEN"] = token
    except Exception as exc:  # never block startup on trace-cred resolution
        logger.warning("Could not export local trace credentials: %s", exc)


def _setup_mlflow_experiment() -> None:
    """Give traces a home. On Databricks Apps the default (artifact-storage) trace export is
    blocked by egress; MLflow 3 **Unity-Catalog tracing** routes traces into a UC schema over a
    reachable API path. When MLFLOW_TRACE_CATALOG/SCHEMA are set we bind the experiment to that
    UC location (+ a SQL warehouse for trace storage); otherwise fall back to plain experiment
    tracing (local dev / eval)."""
    try:
        trace_location = None
        cat = settings.mlflow_trace_catalog or settings.uc_catalog
        sch = settings.mlflow_trace_schema
        wh = settings.mlflow_tracing_warehouse_id or settings.warehouse_id
        if cat and sch:
            try:
                from mlflow.entities.trace_location import UnityCatalog

                trace_location = UnityCatalog(
                    catalog_name=cat, schema_name=sch,
                    table_prefix=settings.mlflow_trace_table_prefix,
                )
                if wh:
                    os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = wh
            except Exception as exc:  # mlflow < 3.11, or location unsupported
                logger.warning("UC trace location unavailable; default tracing: %s", exc)
                trace_location = None

        if settings.mlflow_experiment_id:
            # Bind to the explicitly-configured (shared project) experiment. Attaching a UC trace
            # destination needs the experiment NAME, so resolve it from the id. NOTE: the target
            # experiment must have NO existing traces for a UC destination to bind.
            if trace_location is not None:
                exp = mlflow.get_experiment(settings.mlflow_experiment_id)
                mlflow.set_experiment(experiment_name=exp.name, trace_location=trace_location)
            else:
                mlflow.set_experiment(experiment_id=settings.mlflow_experiment_id)
        elif mlflow.get_tracking_uri() == "databricks":
            from databricks.sdk import WorkspaceClient

            me = WorkspaceClient().current_user.me().user_name
            # No explicit experiment → a dedicated per-user experiment (UC dest needs trace-free).
            name = f"/Users/{me}/supply-chain-planner-uc" if trace_location is not None else f"/Users/{me}/supply-chain-planner"
            if trace_location is not None:
                mlflow.set_experiment(experiment_name=name, trace_location=trace_location)
            else:
                mlflow.set_experiment(name)
        if trace_location is not None:
            logger.info("MLflow UC tracing → %s.%s (prefix=%s) on experiment %s",
                        cat, sch, settings.mlflow_trace_table_prefix,
                        settings.mlflow_experiment_id or f"/Users/.../supply-chain-planner-uc")
    except Exception as exc:  # never let trace config crash the server
        logger.warning("Could not set MLflow experiment; traces may not be recorded: %s", exc)


_export_local_trace_credentials()
_setup_mlflow_experiment()

LAKEBASE_CONFIG = init_lakebase_config()

_LAKEBASE_ERROR_KEYWORDS = (
    "lakebase",
    "pg_hba",
    "postgres",
    "database instance",
    "insufficient privilege",
)


# ── Request parsing ──────────────────────────────────────────────────────────────────────

def _get_user_id(request: ResponsesAgentRequest) -> Optional[str]:
    ci = dict(request.custom_inputs or {})
    if ci.get("user_id"):
        return ci["user_id"]
    if request.context and getattr(request.context, "user_id", None):
        return request.context.user_id
    return None


def _get_thread_id(request: ResponsesAgentRequest) -> str:
    ci = dict(request.custom_inputs or {})
    if ci.get("thread_id"):
        return str(ci["thread_id"])
    if request.context and getattr(request.context, "conversation_id", None):
        return str(request.context.conversation_id)
    return uuid.uuid4().hex


def _latest_user_text(request: ResponsesAgentRequest) -> str:
    """Extract the planner's question from the last user input item."""
    for item in reversed(request.input or []):
        d = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        if d.get("role") != "user":
            continue
        content = d.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return " ".join(p for p in parts if p).strip()
    return ""


def _resume_command(request: ResponsesAgentRequest, user_id: str) -> Optional[Command]:
    """If the request carries an HITL verdict, build a Command(resume=HITLDecision)."""
    ci = dict(request.custom_inputs or {})
    verdict = ci.get("hitl_verdict")
    if not verdict:
        return None
    return Command(
        resume=HITLDecision(
            verdict=HITLVerdict(verdict),
            note=ci.get("hitl_note"),
            user_id=user_id or "unknown",
        ).model_dump()
    )


# ── Output shaping ───────────────────────────────────────────────────────────────────────

def _render_recommendation(rec) -> str:
    if rec is None:
        return "No recommendation was produced."
    lines = [rec.summary, ""]
    if rec.actions:
        lines.append("Proposed actions:")
        lines += [f"  {i}. {a}" for i, a in enumerate(rec.actions, 1)]
    # est_cost / needs_approval are surfaced by the UI as a grey footnote (from custom_outputs),
    # not inline in the assistant text.
    return "\n".join(lines).rstrip()


def _custom_outputs(state: dict, interrupt_payload: Any | None) -> dict[str, Any]:
    rec = state.get("recommendation")
    hitl = state.get("hitl_decision")
    rd = state.get("route_decision")
    out: dict[str, Any] = {
        "trace_notes": state.get("trace_notes", []),
        "route_decision": rd.model_dump() if rd else None,
        "recommendation": rec.model_dump() if rec else None,
        "hitl_decision": hitl.model_dump() if hitl else None,
        "status": "awaiting_approval" if interrupt_payload is not None else "completed",
    }
    if interrupt_payload is not None:
        out["approval_request"] = interrupt_payload  # the planner's interrupt() payload
    return out


def _final_text(state: dict, interrupt_payload: Any | None) -> str:
    if interrupt_payload is not None:
        rec = (interrupt_payload or {}).get("recommendation") if isinstance(interrupt_payload, dict) else None
        summary = rec.get("summary") if isinstance(rec, dict) else None
        return (
            "Approval required before committing this recommendation"
            + (f": {summary}" if summary else ".")
            + "\nResume with an HITL verdict to approve or reject."
        )
    return _render_recommendation(state.get("recommendation"))


# ── Handlers ─────────────────────────────────────────────────────────────────────────────

@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    thread_id = _get_thread_id(request)
    mlflow.update_current_trace(metadata={"mlflow.trace.session": thread_id})

    user_id = _get_user_id(request)
    if not user_id:
        logger.warning("No user_id provided — OBO scope + long-term memory unavailable.")

    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if user_id:
        config["configurable"]["user_id"] = user_id

    resume = _resume_command(request, user_id or "unknown")
    question = _latest_user_text(request)
    graph_input: Any = resume if resume is not None else {
        "question": question,
        "user_id": user_id or "unknown",
        # WS1 short-term memory: record the user turn so the planner's history block (and the
        # next turn on this thread) sees it. Not added on resume — that continues an existing turn.
        "messages": [HumanMessage(content=question)],
    }

    try:
        async with acquire_lakebase_resources(LAKEBASE_CONFIG) as (checkpointer, store):
            config["configurable"]["store"] = store
            graph = build_graph(checkpointer=checkpointer)

            # Drive the graph to completion (or to the HITL interrupt). stream_mode includes
            # "updates" (interrupt detection) and "custom" (intra-node progress); for MVP the
            # custom chunks are ignored here — the single final event is unchanged. Autolog
            # records the full run as one trace either way.
            interrupt_payload: Any | None = None
            async for mode, chunk in graph.astream(
                graph_input, config, stream_mode=["updates", "custom"]
            ):
                if mode == "updates" and "__interrupt__" in chunk:
                    intr = chunk["__interrupt__"]
                    interrupt_payload = intr[0].value if intr else None

            snapshot = await graph.aget_state(config)
            state = dict(snapshot.values) if snapshot else {}

            text = _final_text(state, interrupt_payload)
            item = create_text_output_item(text=text, id=uuid.uuid4().hex)
            item["status"] = "completed"
            # Carry the same structured payload the /invocations path returns (route,
            # recommendation, approval_request, status) on the final stream event so the UI can
            # render the HITL approval card from a streamed run — not just the assistant text.
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=item,
                custom_outputs=_custom_outputs(state, interrupt_payload),
            )
    except Exception as e:
        if any(k in str(e).lower() for k in _LAKEBASE_ERROR_KEYWORDS):
            logger.error("Lakebase access error: %s", e)
            raise HTTPException(
                status_code=503,
                detail=get_lakebase_access_error_message(LAKEBASE_CONFIG.description),
            ) from e
        raise


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    thread_id = _get_thread_id(request)
    user_id = _get_user_id(request)
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if user_id:
        config["configurable"]["user_id"] = user_id

    resume = _resume_command(request, user_id or "unknown")
    question = _latest_user_text(request)
    graph_input: Any = resume if resume is not None else {
        "question": question,
        "user_id": user_id or "unknown",
        # WS1 short-term memory: record the user turn so the planner's history block (and the
        # next turn on this thread) sees it. Not added on resume — that continues an existing turn.
        "messages": [HumanMessage(content=question)],
    }

    try:
        async with acquire_lakebase_resources(LAKEBASE_CONFIG) as (checkpointer, store):
            config["configurable"]["store"] = store
            graph = build_graph(checkpointer=checkpointer)

            interrupt_payload: Any | None = None
            async for chunk in graph.astream(graph_input, config, stream_mode="updates"):
                if "__interrupt__" in chunk:
                    intr = chunk["__interrupt__"]
                    interrupt_payload = intr[0].value if intr else None

            snapshot = await graph.aget_state(config)
            state = dict(snapshot.values) if snapshot else {}

            item = create_text_output_item(text=_final_text(state, interrupt_payload), id=uuid.uuid4().hex)
            item["status"] = "completed"
            return ResponsesAgentResponse(
                output=[item],
                custom_outputs=_custom_outputs(state, interrupt_payload),
            )
    except Exception as e:
        if any(k in str(e).lower() for k in _LAKEBASE_ERROR_KEYWORDS):
            raise HTTPException(
                status_code=503,
                detail=get_lakebase_access_error_message(LAKEBASE_CONFIG.description),
            ) from e
        raise
