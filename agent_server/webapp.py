"""Custom web UI mounted onto the agent server's FastAPI app.

The agent already exposes the MLflow Responses API (`/invocations` + run/poll/resume) via
`LongRunningAgentServer`. This module adds the *human* surface on the SAME app (one Databricks
App, the graph in-process per CLAUDE.md):

  • a one-page React SPA (Claude-style chat) served from `frontend/dist`,
  • `/api/me`          — the OBO caller identity (who the user is),
  • `/api/sessions`    — that user's conversation history (kept in the LangGraph store),
  • `/api/explorer`    — deep links + live peeks into every backend piece the demo uses
                          (Lakebase project, pgvector table, MLflow experiment, Vector Search
                          index, Genie space, Unity Catalog tables).

Auth model: Databricks Apps forward the caller's identity + OAuth token as `X-Forwarded-*`
headers. UC reads in the explorer run **on-behalf-of the user** (their token), so row/column
governance is the user's. The agent itself + Lakebase use the app service principal.

`start_server.py` imports this module so the routes register on `app`. Build the SPA with
`npm --prefix frontend ci && npm --prefix frontend run build` (or use the `start-app` launcher).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent_server.config import settings
from agent_server.start_server import app  # the LongRunningAgentServer FastAPI app

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPA_DIR = _REPO_ROOT / "frontend" / "dist"

router = APIRouter(prefix="/api", tags=["webapp"])


# ── OBO capture ─────────────────────────────────────────────────────────────────────────────
# Pure ASGI middleware (not BaseHTTPMiddleware — that runs the endpoint in a separate task and
# loses contextvars). Captures the Databricks Apps forwarded user token into the OBO contextvar
# for EVERY request (incl. /invocations), so the Knowledge/Genie tools call as the user.

class _OBOTokenMiddleware:
    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            from agent_server.obo import set_obo_token

            token = None
            for k, v in scope.get("headers", []):
                if k == b"x-forwarded-access-token":
                    token = v.decode("latin-1")
                    break
            set_obo_token(token)
        await self.asgi_app(scope, receive, send)


app.add_middleware(_OBOTokenMiddleware)


# ── Identity (OBO) ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Caller:
    email: str
    user_id: Optional[str]
    access_token: Optional[str]  # None only in local-dev
    is_local: bool


def caller_identity(request: Request) -> Caller:
    """Resolve the forwarded Databricks Apps user identity, with a local-dev fallback.

    On the deployed App the proxy injects `X-Forwarded-Access-Token` + `X-Forwarded-Email`;
    locally we fall back to the profile user so the UI boots under uvicorn.
    """
    h = request.headers
    token = h.get("x-forwarded-access-token")
    email = h.get("x-forwarded-email") or h.get("x-forwarded-preferred-username")
    user_id = h.get("x-forwarded-user")
    if token and email:
        return Caller(email=email, user_id=user_id, access_token=token, is_local=False)

    # Local dev: no Apps proxy in front. Use the profile user.
    email = _local_user() or "local-dev@example.com"
    return Caller(email=email, user_id=None, access_token=None, is_local=True)


@lru_cache(maxsize=1)
def _local_user() -> Optional[str]:
    try:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient().current_user.me().user_name
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=1)
def _workspace_host() -> str:
    """Workspace base URL for building deep links (no trailing slash)."""
    try:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient().config.host.rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def _obo_client(caller: Caller):
    """A WorkspaceClient acting as the caller (OBO) on the App; the app principal locally."""
    from databricks.sdk import WorkspaceClient

    if caller.access_token and not caller.is_local:
        # auth_type="pat" required on Apps — the SP's DATABRICKS_CLIENT_ID/SECRET are in the env,
        # so passing token= alone trips Config._validate()'s "more than one authorization method"
        # guard. Pinning it forces the caller's forwarded token (see agent_server.obo).
        return WorkspaceClient(host=_workspace_host(), token=caller.access_token, auth_type="pat")
    return WorkspaceClient()


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    c = caller_identity(request)
    return {
        "email": c.email,
        "user_id": c.user_id,
        "is_local": c.is_local,
        "workspace_host": _workspace_host(),
    }


# ── Backend explorer — "peer into every component" ──────────────────────────────────────────

def _resource_cards() -> list[dict[str, Any]]:
    """Static-ish metadata + deep links for each backend piece. Live peeks are separate routes."""
    host = _workspace_host()
    cat, sch = settings.uc_catalog, settings.uc_schema
    proj = settings.lakebase_autoscaling_project
    branch = settings.lakebase_autoscaling_branch
    cards: list[dict[str, Any]] = []

    # 1 — Lakebase (autoscaling Postgres: agent memory + operational pgvector live here)
    cards.append({
        "key": "lakebase",
        "title": "Lakebase",
        "subtitle": "Autoscaling Postgres — agent memory + operational rows",
        "accent": "navy",
        "facts": {
            "project": proj or settings.lakebase_instance_name or "—",
            "branch": branch or "—",
            "database": settings.lakebase_database,
            "memory schema": settings.lakebase_memory_schema,
            "operational schema": settings.lakebase_operational_schema,
        },
        "link": f"{host}/compute/database-instances" if host else None,
        "link_label": "Open Lakebase",
    })
    # 2 — pgvector operational table (the hybrid-query differentiator)
    cards.append({
        "key": "pgvector",
        "title": "pgvector",
        "subtitle": "quality_incidents — vector(1024) + HNSW, joined in-query to live rows",
        "accent": "lava",
        "facts": {
            "table": f"{settings.lakebase_operational_schema}.quality_incidents",
            "index": "HNSW (cosine)",
            "embedding": settings.embedding_endpoint,
        },
        "peek": "/api/explorer/pgvector",
        "link": f"{host}/compute/database-instances" if host else None,
        "link_label": "Open in Lakebase",
    })
    # 3 — MLflow experiment (the trace of every run)
    exp = settings.mlflow_experiment_id
    cards.append({
        "key": "experiment",
        "title": "MLflow Experiment",
        "subtitle": "Every run is one trace — routing, retrieval, planner, gate, HITL",
        "accent": "blue",
        "facts": {"experiment_id": exp or "(auto per-user)"},
        "link": (f"{host}/ml/experiments/{exp}" if (host and exp) else (f"{host}/ml/experiments" if host else None)),
        "link_label": "Open experiment",
    })
    # 4 — Vector Search (Knowledge agent / RAG corpus)
    vs_idx = settings.vector_search_index
    vs_link = None
    if host and vs_idx and vs_idx.count(".") == 2:
        c2, s2, i2 = vs_idx.split(".")
        vs_link = f"{host}/explore/data/{c2}/{s2}/{i2}"
    cards.append({
        "key": "vector_search",
        "title": "Vector Search",
        "subtitle": "Mosaic AI VS index — the large unstructured knowledge corpus",
        "accent": "green",
        "facts": {
            "endpoint": settings.vector_search_endpoint or "—",
            "index": vs_idx or "(not configured)",
        },
        "link": vs_link or (f"{host}/ml/vector-search" if host else None),
        "link_label": "Open Vector Search",
    })
    # 5 — Genie (Analytics agent)
    gid = settings.genie_space_id
    cards.append({
        "key": "genie",
        "title": "Genie Space",
        "subtitle": "NL→SQL analytics over the governed operational tables",
        "accent": "yellow",
        "facts": {"space_id": gid or "(not configured)"},
        "link": (f"{host}/genie/rooms/{gid}" if (host and gid) else None),
        "link_label": "Open Genie",
    })
    # 6 — Unity Catalog tables (the governed source data)
    cards.append({
        "key": "uc_tables",
        "title": "Unity Catalog",
        "subtitle": f"Governed source tables in {cat}.{sch}",
        "accent": "maroon",
        "facts": {"catalog": cat, "schema": sch},
        "peek": "/api/explorer/uc-tables",
        "link": f"{host}/explore/data/{cat}/{sch}" if host else None,
        "link_label": "Open Catalog Explorer",
    })
    return cards


@router.get("/explorer")
def explorer() -> dict[str, Any]:
    return {"workspace_host": _workspace_host(), "cards": _resource_cards()}


@router.get("/explorer/uc-tables")
def uc_tables(request: Request) -> dict[str, Any]:
    """List tables in the operational UC schema, on-behalf-of the caller (governance applies)."""
    caller = caller_identity(request)
    try:
        w = _obo_client(caller)
        tables = list(w.tables.list(catalog_name=settings.uc_catalog, schema_name=settings.uc_schema))
        rows = [{
            "name": t.name,
            "full_name": t.full_name,
            "type": getattr(t.table_type, "value", str(t.table_type)) if t.table_type else None,
            "columns": len(t.columns or []),
            "comment": t.comment,
        } for t in tables]
        return {"catalog": settings.uc_catalog, "schema": settings.uc_schema, "tables": rows, "obo": not caller.is_local}
    except Exception as exc:  # noqa: BLE001
        logger.warning("uc-tables peek failed: %s", exc)
        return {"catalog": settings.uc_catalog, "schema": settings.uc_schema, "tables": [], "error": str(exc)}


def _app_creds_conn_str() -> str:
    """Resolve a Lakebase psycopg conninfo using APP creds (short-lived OAuth DB credential).
    Shared by the explorer peeks + the Meridian state-read endpoint so the connection form can't
    drift. Local dev resolves via the configured profile; on the App the SP supplies creds."""
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    ep = settings.lakebase_autoscaling_endpoint or "primary"
    full_ep = ep if ep.startswith("projects/") else (
        f"projects/{settings.lakebase_autoscaling_project}/branches/"
        f"{settings.lakebase_autoscaling_branch}/endpoints/{ep}"
    )
    endpoint = w.postgres.get_endpoint(name=full_ep)
    host = endpoint.status.hosts.host
    cred = w.postgres.generate_database_credential(endpoint=full_ep)
    return (
        f"host={host} dbname={settings.lakebase_database} "
        f"user={w.current_user.me().user_name} password={cred.token} sslmode=require"
    )


@router.get("/explorer/pgvector")
def pgvector_peek() -> dict[str, Any]:
    """Live peek at the operational pgvector table (count + a few rows). Uses app creds."""
    schema = settings.lakebase_operational_schema
    try:
        import psycopg

        with psycopg.connect(_app_creds_conn_str(), connect_timeout=10) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {schema}.quality_incidents")
            total = cur.fetchone()[0]
            cur.execute(
                f"SELECT incident_id, supplier_id, sku, category, severity, summary "
                f"FROM {schema}.quality_incidents WHERE expired_at IS NULL "
                f"ORDER BY incident_date DESC LIMIT 8"
            )
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return {"table": f"{schema}.quality_incidents", "active_rows": total, "sample": rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning("pgvector peek failed: %s", exc)
        return {"table": f"{schema}.quality_incidents", "sample": [], "error": str(exc)}


# ── Session history (per user) — kept in the LangGraph store ────────────────────────────────
# The store (AsyncDatabricksStore) is already open on the app; we use it as a small KV index of
# the user's conversations under namespace ("ui_sessions", <email>). Each item is one thread.

def _store():
    store = getattr(app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Lakebase store not ready")
    return store


# ── Lakebase connection resilience ──────────────────────────────────────────────────────────
# Lakebase autoscaling scales to zero when idle and rotates OAuth DB creds hourly. When that
# happens the long-lived pool holds connections the server has already terminated, so the first
# request from a user after an idle period grabs a dead connection and fails with one of these.
# The pool discards the BAD connection (self-heals) — so simply RETRYING gets a fresh one.
_TRANSIENT_DB_MARKERS = (
    "terminating connection due to administrator command",  # 57P01 — scale-to-zero / restart
    "ssl connection has been closed unexpectedly",
    "consuming input failed",
    "connection is lost",
    "the connection is closed",
    "connection already closed",
    "server closed the connection unexpectedly",
    "bad connection",
)


def _is_transient_db_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if any(m in msg for m in _TRANSIENT_DB_MARKERS):
        return True
    try:
        import psycopg

        return isinstance(exc, psycopg.OperationalError)
    except Exception:  # noqa: BLE001
        return False


async def _with_db_retry(make_coro, *, attempts: int = 3, base_delay: float = 0.4):
    """Run an async Lakebase op, retrying transient connection drops. `make_coro` is a 0-arg
    callable returning a FRESH coroutine each attempt (a coroutine can't be awaited twice)."""
    for i in range(attempts):
        try:
            return await make_coro()
        except Exception as exc:  # noqa: BLE001
            if not _is_transient_db_error(exc) or i == attempts - 1:
                raise
            logger.info("transient Lakebase error (attempt %d/%d): %s", i + 1, attempts, exc)
            await asyncio.sleep(base_delay * (i + 1))


async def _warm_lakebase(checkpointer, store, config) -> None:
    """Best-effort: force the pools to drop stale (idled / rotated) connections and pick up fresh
    ones BEFORE the graph run, so a new-after-idle user's chat doesn't die mid-stream. Retries
    transient drops; ignores anything else (the real run surfaces genuine errors)."""
    for ping in (
        lambda: store.aget(("__healthcheck__",), "ping"),
        lambda: checkpointer.aget_tuple(config),
    ):
        try:
            await _with_db_retry(ping)
        except Exception as exc:  # noqa: BLE001
            logger.info("lakebase warm-up ping skipped: %s", exc)


def _ns_label(email: str) -> str:
    """LangGraph store namespace labels can't contain '.' (or other punctuation) — emails do.
    Sanitize to a deterministic alnum/underscore label so upsert + list use the same namespace."""
    import re

    return re.sub(r"[^A-Za-z0-9_-]", "_", email or "anon")


@router.get("/sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    caller = caller_identity(request)
    try:
        ns = ("ui_sessions", _ns_label(caller.email))
        items = await _with_db_retry(lambda: _store().asearch(ns, limit=100))
        sessions = sorted(
            ({"thread_id": it.key, **(it.value or {})}
             for it in items if not (it.value or {}).get("deleted_by_user")),
            key=lambda s: s.get("updated_at", ""), reverse=True,
        )
        return {"sessions": sessions}
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_sessions failed: %s", exc)
        return {"sessions": [], "error": str(exc)}


@router.put("/sessions/{thread_id}")
async def upsert_session(thread_id: str, request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    caller = caller_identity(request)
    label = _ns_label(caller.email)
    ns = ("ui_sessions", label)
    # Merge with the existing record so omitted fields are preserved — e.g. the inferred title is
    # sent only on the first turn; follow-up turns omit it and must NOT reset it to "New conversation".
    try:
        existing = await _with_db_retry(lambda: _store().aget(ns, thread_id))
        cur = (existing.value if existing else None) or {}
    except Exception:  # noqa: BLE001
        cur = {}
    value = {
        "title": (body.get("title") or cur.get("title") or "New conversation")[:120],
        "updated_at": body.get("updated_at") or cur.get("updated_at") or "",
        "preview": ((body.get("preview") if body.get("preview") is not None else cur.get("preview")) or "")[:200],
    }
    try:
        await _with_db_retry(lambda: _store().aput(ns, thread_id, value))
        # Persist the rendered transcript (separate namespace so the sessions LIST stays light) so a
        # past chat can be reopened in a fresh browser session instead of showing an empty panel.
        msgs = body.get("messages")
        if isinstance(msgs, list):
            tns = ("ui_transcripts", label)
            await _with_db_retry(lambda: _store().aput(tns, thread_id, {"messages": msgs[-200:]}))
    except Exception as exc:  # noqa: BLE001
        logger.warning("upsert_session failed: %s", exc)
    return {"thread_id": thread_id, **value}


@router.get("/sessions/{thread_id}/messages")
async def session_messages(thread_id: str, request: Request) -> dict[str, Any]:
    """Rehydrate a past conversation — returns the persisted transcript for a thread so clicking a
    historical session in the sidebar reopens it (instead of a dead/empty panel)."""
    caller = caller_identity(request)
    tns = ("ui_transcripts", _ns_label(caller.email))
    try:
        item = await _with_db_retry(lambda: _store().aget(tns, thread_id))
        return {"messages": (item.value or {}).get("messages", []) if item else []}
    except Exception as exc:  # noqa: BLE001
        logger.warning("session_messages failed: %s", exc)
        return {"messages": [], "error": str(exc)}


@router.delete("/sessions/{thread_id}")
async def delete_session(thread_id: str, request: Request) -> dict[str, Any]:
    """Soft-delete: flag the session `deleted_by_user` so it's hidden from the history list. The
    data is RETAINED — the session record (with the flag) and the transcript both stay in the
    store; nothing is removed. Re-listing simply filters these out (see list_sessions)."""
    caller = caller_identity(request)
    ns = ("ui_sessions", _ns_label(caller.email))
    try:
        existing = await _with_db_retry(lambda: _store().aget(ns, thread_id))
        value = dict((existing.value if existing else None) or {})
        value["deleted_by_user"] = True
        value["deleted_at"] = datetime.now(timezone.utc).isoformat()
        await _with_db_retry(lambda: _store().aput(ns, thread_id, value))
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_session failed: %s", exc)
        return {"thread_id": thread_id, "deleted": False, "error": str(exc)}
    return {"thread_id": thread_id, "deleted": True}


@router.get("/_seed_demo_memories")
async def seed_demo_memories(request: Request) -> dict[str, Any]:
    """DEMO-ONLY: write a couple of the caller's "prior decisions" into the long-term store
    (the same curated shape `commit_node` writes — a `memory_text` field embedded via
    `EMBED_INDEX`) so the cross-conversation recall questions — "what did we decide about the
    Acme delay yesterday?", "continue this morning's escalation" — have something to recall.
    Visit once in the browser. Idempotent (overwrites by key)."""
    from agent_server.memory import EMBED_INDEX, MEMORY_TEXT_FIELD, approvals_ns, preferences_ns

    caller = caller_identity(request)
    appr_ns = approvals_ns(caller.email)
    pref_ns = preferences_ns(caller.email)
    # Each value MUST carry `memory_text` — the hydrate node's recall skips items without it and
    # embeds only that field (mirrors `agent_server.memory.build_memory_writes`).
    demos: list[tuple[tuple[str, str], str, dict]] = [
        (appr_ns, "demo-acme-delay", {
            "question": "How should we handle the Acme delivery delay?",
            "verdict": "approved",
            "note": "Decided yesterday — revisit if Acme slips past Friday.",
            MEMORY_TEXT_FIELD: "How should we handle the Acme delivery delay? → approved: Hold "
                               "firm; expedite a 200-unit bridge order of SKU-1001 from DuPont to "
                               "cover the Acme slip.",
        }),
        (appr_ns, "demo-morning-escalation", {
            "question": "Escalate the Henkel SKU-1001 coverage gap this morning?",
            "verdict": "approved",
            "note": "This morning's escalation — pending supplier confirmation.",
            MEMORY_TEXT_FIELD: "Escalate the Henkel SKU-1001 coverage gap this morning? → "
                               "approved: Escalated SKU-1001 quality containment — prioritize the "
                               "Henkel cracking cluster; 40 on-hand vs a 500-unit open PO.",
        }),
        (pref_ns, "demo-pref-bridge-order", {
            "question": "How should we handle the Acme delivery delay?",
            "source_thread": "demo-acme-delay",
            MEMORY_TEXT_FIELD: "Approved approach: Hold firm and expedite a bridge order from an "
                               "alternate supplier (DuPont) to cover a supplier slip | actions: "
                               "Expedite a 200-unit reorder of SKU-1001; hold the existing PO.",
        }),
    ]
    store = _store()
    written: list[str] = []
    for ns, key, value in demos:
        try:
            await _with_db_retry(
                lambda n=ns, k=key, v=value: store.aput(n, k, v, index=EMBED_INDEX)
            )
            written.append(f"{ns[0]}/{key}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("seed memory %s failed: %s", key, exc)
    return {"written": written}


# ── Meridian state read (committed write-back rows + recalled memory for one thread) ─────────
# The UI's "Lakebase" tab reads back the structured rows the human's commit wrote, plus the
# semantic memory the decision was embedded into. Each query is guarded → [] (so a thread that
# hasn't committed yet, or a table that doesn't exist locally, renders cleanly).

_STATE_TABLE_QUERIES = {
    "approved_actions": (
        "SELECT action_key, kind, po_id, supplier_id, sku, qty, cost_delta, status, rationale, "
        "user_id, created_at FROM {schema}.approved_actions WHERE thread_id = %s ORDER BY created_at"
    ),
    "planning_parameters": (
        "SELECT sku, parameter, old_value, new_value, rationale, user_id, created_at "
        "FROM {schema}.planning_parameters WHERE thread_id = %s ORDER BY created_at"
    ),
    "constraints": (
        "SELECT constraint_key, kind, sku, program, detail, rationale, user_id, created_at "
        "FROM {schema}.constraints WHERE thread_id = %s ORDER BY created_at"
    ),
}


@router.get("/state/tables")
async def state_tables(request: Request, thread_id: str) -> dict[str, Any]:
    """Read the Meridian write-back rows for one thread + the caller's recalled approval memory."""
    caller = caller_identity(request)
    # Write-back tables (approved_actions / planning_parameters / constraints) live in the SP-owned
    # write-back schema, NOT the operational/synced schema (`public`). See operational_db._WRITEBACK_SCHEMA.
    schema = settings.lakebase_writeback_schema
    result: dict[str, Any] = {
        "thread_id": thread_id,
        "approved_actions": [],
        "planning_parameters": [],
        "constraints": [],
        "recalled_memory": [],
    }

    # 1) Relational write-back rows (app creds psycopg). Guard each query so a missing table (not
    #    yet created locally) or an empty thread degrades to [] + a per-table error note.
    try:
        import psycopg

        with psycopg.connect(_app_creds_conn_str(), connect_timeout=10) as conn:
            for table, sql in _STATE_TABLE_QUERIES.items():
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql.format(schema=schema), (thread_id,))
                        cols = [d.name for d in cur.description]
                        result[table] = [dict(zip(cols, r)) for r in cur.fetchall()]
                except Exception as exc:  # noqa: BLE001 — e.g. table doesn't exist yet
                    conn.rollback()
                    result[table] = []
                    result.setdefault("errors", {})[table] = str(exc)
    except Exception as exc:  # noqa: BLE001 — connection failed entirely
        logger.warning("state/tables read failed: %s", exc)
        result.setdefault("errors", {})["connection"] = str(exc)

    # 2) Recalled long-term memory (the decision's embedded approval text), via the open store.
    try:
        from agent_server.memory import recall_approvals

        store = _store()
        # Recall approvals semantically related to what was committed in this thread — use the
        # committed rationale(s) as the search query, not the opaque thread_id (which is not a
        # meaningful embedding query). Falls back to "" when nothing was written.
        recall_query = " ".join(
            str(r.get("rationale") or "") for r in result["approved_actions"]
        ).strip()
        items = await _with_db_retry(
            lambda: recall_approvals(
                store, caller.email, recall_query, settings.memory_recall_limit, None
            )
        )
        result["recalled_memory"] = [
            {"text": m.text, "score": m.score, "namespace": m.namespace} for m in (items or [])
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("state/tables memory recall failed: %s", exc)
        result.setdefault("errors", {})["recalled_memory"] = str(exc)

    return result


# ── Streaming chat (SSE) ──────────────────────────────────────────────────────────────────
# Drives the graph and streams live step progress (route → gather → plan → gate) + the final
# answer. Runs behind the OBO middleware (so Knowledge/Genie call as the user) and reuses the
# agent's graph + output shaping — it does not modify the team's /invocations handler.

_STEP_LABELS = {
    "supervisor": "Routing the question…",
    "gather_knowledge": "Searching the knowledge corpus…",
    "gather_analytics": "Querying Genie…",
    "gather_operational": "Running the operational query…",
    "hydrate_memory": "Recalling prior decisions…",
    "planner": "Composing the recommendation…",
    "hitl_review": "Preparing the approval…",
    "commit": "Finalizing…",
}


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, default=str)}\n\n".encode("utf-8")


@router.post("/chat/stream")
async def chat_stream(request: Request, body: dict[str, Any] = Body(default={})):
    caller = caller_identity(request)
    question = (body.get("question") or "").strip()
    thread_id = str(body.get("thread_id") or uuid.uuid4().hex)
    verdict = body.get("verdict")  # "approved" | "rejected" → resume a paused HITL run
    note = body.get("note")
    rationale = body.get("rationale")
    # Per-action review payload: accept either `action_decisions` or a `decisions` alias.
    action_decisions = body.get("action_decisions") or body.get("decisions") or []
    user_id = caller.email

    async def gen():
        try:
            from agent_server.agent import LAKEBASE_CONFIG, _custom_outputs, _final_text, _trace_url
            from agent_server.contracts import HITLDecision, HITLVerdict
            from agent_server.graph.build_graph import build_graph
            from agent_server.lakebase import acquire_lakebase_resources
            from langgraph.types import Command

            # Resume the paused run with the verdict, or start a fresh question.
            if verdict:
                # Enforce the required rationale at this edge: an approved Meridian commit must carry
                # the human's reasoning. Surface as an SSE error frame before driving the graph.
                if str(verdict) == "approved" and not (rationale and str(rationale).strip()):
                    yield _sse({"type": "error", "error": "A rationale is required to approve."})
                    return
                graph_input: Any = Command(resume=HITLDecision(
                    verdict=HITLVerdict(verdict), user_id=user_id, note=note,
                    rationale=rationale, action_decisions=action_decisions,
                ).model_dump())
                yield _sse({"type": "step", "label": f"Recording {verdict} decision…"})
            else:
                graph_input = {"question": question, "user_id": user_id}
                yield _sse({"type": "step", "label": "Starting…"})

            async with acquire_lakebase_resources(LAKEBASE_CONFIG) as (checkpointer, store):
                config = {"configurable": {"thread_id": thread_id, "user_id": user_id, "store": store}}
                # Refresh stale pooled connections (scale-to-zero / hourly token rotation) before
                # the run so a user arriving after idle doesn't get a connection drop mid-stream.
                await _warm_lakebase(checkpointer, store, config)
                graph = build_graph(checkpointer=checkpointer)
                interrupt_payload = None
                seen: set[str] = set()
                async for mode, chunk in graph.astream(
                    graph_input, config, stream_mode=["updates", "custom"]
                ):
                    if mode == "updates":
                        if "__interrupt__" in chunk:
                            intr = chunk["__interrupt__"]
                            interrupt_payload = intr[0].value if intr else None
                            continue
                        for node in chunk.keys():
                            if node in _STEP_LABELS and node not in seen:
                                seen.add(node)
                                yield _sse({"type": "step", "node": node, "label": _STEP_LABELS[node]})
                    elif mode == "custom":
                        kind = chunk.get("kind")
                        if kind == "route":
                            yield _sse({"type": "route", "agents": chunk.get("agents"),
                                        "reasoning": chunk.get("reasoning")})
                        elif kind == "substep":
                            yield _sse({"type": "substep", "node": chunk.get("node"),
                                        "label": chunk.get("label")})
                        elif kind == "trace":
                            yield _sse({"type": "trace", "note": chunk.get("note")})
                        # unknown kind → ignore
                snapshot = await graph.aget_state(config)
                state = dict(snapshot.values) if snapshot else {}
                trace_id = None
                try:
                    import mlflow

                    trace_id = mlflow.get_last_active_trace_id()
                except Exception:  # noqa: BLE001
                    pass
                yield _sse({
                    "type": "done",
                    "thread_id": thread_id,
                    "trace_id": trace_id,  # client attaches 👍/👎 feedback to this trace
                    "trace_url": _trace_url(trace_id),  # ready-made workspace deep-link (avoids the 404 path)
                    "text": _final_text(state, interrupt_payload),
                    "extras": _custom_outputs(state, interrupt_payload),
                })
        except Exception as exc:  # surface as a stream error, never a 500 mid-stream
            logger.warning("chat_stream failed: %s", exc)
            yield _sse({"type": "error", "error": str(exc)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        # X-Accel-Buffering: no is load-bearing — keeps the Apps proxy from buffering the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/feedback")
def feedback(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Log 👍/👎 (and an optional comment) as an MLflow assessment on the run's trace, so feedback
    is queryable alongside traces for eval. The human is recorded as the source; the write is the
    app SP (which has Can Edit on the trace experiment)."""
    caller = caller_identity(request)
    trace_id = body.get("trace_id")
    if not trace_id:
        raise HTTPException(400, "trace_id is required")
    try:
        import mlflow
        from mlflow.entities import AssessmentSource

        source = AssessmentSource(source_type="HUMAN", source_id=caller.email)
        mlflow.log_feedback(trace_id=trace_id, name="thumbs_up", value=bool(body.get("value")), source=source)
        if body.get("comment"):
            mlflow.log_feedback(trace_id=trace_id, name="comment", value=str(body["comment"]), source=source)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback failed: %s", exc)
        return {"ok": False, "error": str(exc)}


app.include_router(router)


# Bare "/" → the SPA at /ui (the built assets use the /ui/ base path, so we redirect rather
# than mount at root). With the chat proxy disabled, "/" is otherwise unhandled → 503.
@app.get("/")
def _root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


# ── Serve the SPA (built React app). Mounted last so /api/* + /invocations win. ─────────────
if _SPA_DIR.exists():
    # Served at /ui to avoid clobbering the agent server's own chat proxy at "/". To make this
    # the App root, set enable_chat_proxy=False in start_server.py and mount at "/".
    app.mount("/ui", StaticFiles(directory=str(_SPA_DIR), html=True), name="spa")
    logger.info("Mounted SPA at /ui from %s", _SPA_DIR)
else:
    @app.get("/ui")
    def _spa_missing() -> JSONResponse:  # pragma: no cover
        return JSONResponse(
            status_code=503,
            content={"detail": "SPA not built. Run: npm --prefix frontend ci && npm --prefix frontend run build"},
        )
