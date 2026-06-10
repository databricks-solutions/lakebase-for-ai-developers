"""Run paths + trace access for the eval flywheel.

Two ways to produce a prediction (chosen by `--layer`), both returning the SAME flattened decision
dict so the judges / gate scorer apply unchanged:

- **graph** — `run_agent(query)` runs the compiled graph in-process (today's path). MLflow autolog
  records the LangGraph node spans on the eval-process trace.
- **server** — `run_via_server(query, base_url)` POSTs to a running local agent-server's
  `/invocations` with `x-mlflow-return-trace-id: true`, so the response carries the authoritative
  server-process trace id (full FastAPI→graph span tree, real latency + tokens). We then read that
  trace back with `fetch_trace`.

Plus `summarize_trace(trace)` → `TraceSummary` (the single place that knows the MLflow Trace shape),
which the trace scorers in `scorers.py` consume.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import mlflow
from langgraph.checkpoint.memory import MemorySaver

from agent_server.config import settings
from agent_server.evaluation.scorers import TraceSummary
from agent_server.graph.build_graph import build_graph

# Autolog so each in-process prediction is one trace (mirrors the server). Quiet the chatty logger.
mlflow.langchain.autolog()

_EXPERIMENT_ID: str | None = None  # set by _setup_experiment(); used for the session-search fallback


def _pin_oauth_token() -> None:
    """Mint the profile's OAuth token ONCE and pin it as a static bearer for this process.

    Why: the eval fans out the graph's three gather nodes concurrently *and* scores rows
    concurrently. Under the `databricks-cli` auth provider each thread shells out
    `databricks auth token --force-refresh`, and concurrent refreshes corrupt the shared CLI token
    cache (`exit status 45`). A failed fetch then sends an un-credentialed request, so the trace /
    LLM-judge APIs return `401: Credential was not sent`. Pinning a static bearer removes the
    per-call subprocess entirely — it's still the profile's OAuth token, just fetched once (≈1h
    lifetime, ample for an eval run). No-op on Databricks (ambient SP) or when a token is already
    set. Keeps the eval on OAuth while making it concurrency-safe."""
    if settings.on_databricks or os.environ.get("DATABRICKS_TOKEN"):
        return
    profile = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    if not profile:
        return
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient(profile=profile)
        token = w.config.authenticate().get("Authorization", "").split(" ", 1)[-1]
        if token:
            os.environ["DATABRICKS_HOST"] = w.config.host
            os.environ["DATABRICKS_TOKEN"] = token
            os.environ.pop("DATABRICKS_CONFIG_PROFILE", None)  # force static-bearer (pat) auth
            print(f"  (pinned OAuth token from profile '{profile}' → static bearer for the run)")
    except Exception as exc:
        print(f"  (oauth-pin note: {str(exc)[:100]})")


def _setup_experiment() -> None:
    global _EXPERIMENT_ID
    try:
        if settings.mlflow_experiment_id:
            mlflow.set_experiment(experiment_id=settings.mlflow_experiment_id)
            _EXPERIMENT_ID = settings.mlflow_experiment_id
        elif mlflow.get_tracking_uri() == "databricks":
            from databricks.sdk import WorkspaceClient

            me = WorkspaceClient().current_user.me().user_name
            exp = mlflow.set_experiment(f"/Users/{me}/supply-chain-planner")
            _EXPERIMENT_ID = getattr(exp, "experiment_id", None)
    except Exception as exc:
        print(f"  (experiment setup note: {str(exc)[:80]})")


# ── In-process graph layer ─────────────────────────────────────────────────────────────────────

_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph(checkpointer=MemorySaver())
    return _GRAPH


async def _arun(query: str) -> dict:
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex, "user_id": "eval@databricks.com"}}
    g = _graph()
    await g.ainvoke({"question": query, "user_id": "eval@databricks.com"}, cfg)
    snap = await g.aget_state(cfg)
    return dict(snap.values) if snap else {}


def _evidence_summary(state: dict) -> str:
    parts = []
    if (or_ := state.get("operational_result")) and or_.rows:
        parts.append("operational: " + "; ".join(
            f"{r.supplier_id}/{r.sku} sim={r.similarity} on_hand={r.on_hand_qty} open_po={r.open_po_qty}"
            for r in or_.rows[:3]))
    if (ar := state.get("analytics_result")) and ar.rows:
        parts.append(f"analytics: {ar.answer or ''} {ar.rows[:4]}")
    if (kr := state.get("knowledge_result")) and kr.passages:
        parts.append("knowledge: " + "; ".join(p.source for p in kr.passages[:3]))
    return " | ".join(parts) or "no gather results"


def _decision_from_state(state: dict) -> dict:
    rd = state.get("route_decision")
    rec = state.get("recommendation")
    return {
        "route": rd.agents if rd else [],
        "route_reasoning": rd.reasoning if rd else None,
        "summary": rec.summary if rec else None,
        "actions": rec.actions if rec else [],
        "needs_approval": rec.needs_approval if rec else None,
        "is_action_bearing": rec.is_action_bearing if rec else None,
        "est_cost_usd": rec.est_cost_usd if rec else None,
        "evidence": _evidence_summary(state),
    }


@mlflow.trace
def run_agent(query: str) -> dict:
    """predict_fn for the in-process (graph) layer — returns the routing + recommendation decision.
    Also the predict_fn for the legacy `evaluate`/`evaluate_direct` paths."""
    return _decision_from_state(asyncio.run(_arun(query)))


# ── Live-server layer ───────────────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = os.environ.get("FLYWHEEL_BASE_URL", "http://localhost:8000")
_RETURN_TRACE_HEADER = "x-mlflow-return-trace-id"


def _decision_from_custom_outputs(co: dict) -> dict:
    """Flatten the server's custom_outputs into the same shape `run_agent` returns, so the judges /
    gate scorer apply unchanged. The gather rows aren't in custom_outputs, so `evidence` is the
    planner's own citations + reasoning (a weaker grounding signal than the in-process layer's raw
    rows — noted in the report)."""
    co = co or {}
    rd = co.get("route_decision") or {}
    rec = co.get("recommendation") or {}
    if not rec and isinstance(co.get("approval_request"), dict):
        rec = co["approval_request"].get("recommendation") or {}
    citations = rec.get("citations") or []
    evidence = "; ".join(citations) if citations else (rec.get("reasoning") or "no evidence in custom_outputs")
    return {
        "route": rd.get("agents", []),
        "route_reasoning": rd.get("reasoning"),
        "summary": rec.get("summary"),
        "actions": rec.get("actions", []),
        "needs_approval": rec.get("needs_approval"),
        "is_action_bearing": rec.get("is_action_bearing"),
        "est_cost_usd": rec.get("est_cost_usd"),
        "evidence": evidence,
        "status": co.get("status"),
    }


def run_via_server(query: str, base_url: str = DEFAULT_BASE_URL,
                   user_id: str = "eval@databricks.com") -> tuple[dict, str | None, str]:
    """POST one question to a running agent-server's /invocations (synchronous, non-background).

    Returns (decision_dict, trace_id, thread_id). `status == "awaiting_approval"` (the gate
    interrupt) is a valid terminal snapshot — we score the pre-approval state and do NOT resume.
    """
    import requests  # databricks-sdk dep; always present

    thread_id = uuid.uuid4().hex
    body = {
        "input": [{"role": "user", "content": query}],
        "custom_inputs": {"user_id": user_id, "thread_id": thread_id},
    }
    resp = requests.post(
        f"{base_url.rstrip('/')}/invocations",
        json=body,
        headers={"Content-Type": "application/json", _RETURN_TRACE_HEADER: "true"},
        timeout=float(os.environ.get("FLYWHEEL_REQUEST_TIMEOUT", "600")),
    )
    resp.raise_for_status()
    data = resp.json()
    decision = _decision_from_custom_outputs(data.get("custom_outputs") or {})
    trace_id = (data.get("metadata") or {}).get("trace_id")
    return decision, trace_id, thread_id


# ── Trace access + normalization ─────────────────────────────────────────────────────────────────

def fetch_trace(trace_id: str, retries: int = 8, delay: float = 1.5):
    """Read a trace by id, retrying for async-export flush lag (server layer logs from another
    process). Returns the Trace or None."""
    if not trace_id:
        return None
    for attempt in range(retries):
        try:
            tr = mlflow.get_trace(trace_id)
            if tr is not None:
                return tr
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def fetch_trace_by_session(thread_id: str, retries: int = 5, delay: float = 1.5):
    """Fallback when no trace id came back: find the run's trace by the session tag the server sets
    (`mlflow.update_current_trace(metadata={"mlflow.trace.session": thread_id})`)."""
    if not thread_id:
        return None
    exp_ids = [_EXPERIMENT_ID] if _EXPERIMENT_ID else None
    flt = f"metadata.`mlflow.trace.session` = '{thread_id}'"
    for attempt in range(retries):
        try:
            df = mlflow.search_traces(experiment_ids=exp_ids, filter_string=flt, max_results=1)
            if df is not None and len(df) > 0:
                tid = df.iloc[0].get("trace_id") or df.iloc[0].get("request_id")
                return mlflow.get_trace(tid)
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def _extract_total_tokens(trace) -> int | None:
    """Total tokens for the run, or None if the endpoint reported no usage."""
    tu = getattr(trace.info, "token_usage", None)
    if isinstance(tu, dict):
        if tu.get("total_tokens") is not None:
            return int(tu["total_tokens"])
        inp, out = tu.get("input_tokens"), tu.get("output_tokens")
        if inp is not None or out is not None:
            return int(inp or 0) + int(out or 0)
    # Fallback: the raw metadata string some backends populate.
    meta = getattr(trace.info, "request_metadata", None) or {}
    raw = meta.get("mlflow.trace.tokenUsage")
    if raw:
        try:
            d = json.loads(raw)
            return int(d.get("total_tokens") or (d.get("input_tokens", 0) + d.get("output_tokens", 0)))
        except Exception:
            return None
    return None


def _span_ok(span) -> bool:
    code = getattr(getattr(span, "status", None), "status_code", None)
    return str(getattr(code, "name", code)).upper().endswith("OK")


def summarize_trace(trace) -> TraceSummary:
    """Normalize an MLflow Trace into the TraceSummary the trace scorers read. The only place that
    touches the Trace object shape."""
    if trace is None:
        return TraceSummary()
    spans = list(getattr(getattr(trace, "data", None), "spans", []) or [])
    names: set[str] = set()
    latency: dict[str, float] = {}
    for s in spans:
        names.add(s.name)
        ms = (s.end_time_ns - s.start_time_ns) / 1e6 if (s.end_time_ns and s.start_time_ns) else 0.0
        latency[s.name] = max(latency.get(s.name, 0.0), ms)  # keep the longest if a name repeats
    state = str(getattr(trace.info, "status", "")).split(".")[-1] or None
    return TraceSummary(
        trace_id=getattr(trace.info, "trace_id", None) or getattr(trace.info, "request_id", None),
        span_names=names,
        span_latency_ms=latency,
        total_latency_ms=float(getattr(trace.info, "execution_time_ms", 0) or 0) or None,
        total_tokens=_extract_total_tokens(trace),
        state=state,
    )


__all__ = [
    "_pin_oauth_token",
    "_setup_experiment",
    "run_agent",
    "run_via_server",
    "fetch_trace",
    "fetch_trace_by_session",
    "summarize_trace",
    "DEFAULT_BASE_URL",
]
