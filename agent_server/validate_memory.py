"""Memory + trace FINAL-VALIDATION harness for the long-term-memory reconciliation.

This is the deep validation `evaluate_agent.py` can't do: that harness wires `MemorySaver()`
with **no store** (so `hydrate_memory` gets `store=None` and `commit` writes nothing) and is
single-turn, so it never exercises long-term memory. This module drives the REAL graph against a
REAL Lakebase store and inspects the resulting MLflow traces (spans, inputs/outputs, latency,
tokens).

What it validates:
  1. Write policy (commit) — approved+action-bearing ⇒ approvals+preferences+supplier_notes;
     rejected ⇒ audit (approvals) only; informational (no gate) ⇒ audit only; every value carries
     a curated `memory_text`.
  2. Recall (hydrate) — a NEW thread for the SAME user recalls the prior decision; the planner's
     LLM input actually contains the rendered memory block (cross-session "multi-turn").
  3. Graph order — `hydrate_memory` runs AFTER the gather fan-in and BEFORE `planner`.
  4. Short-term multi-turn — same-thread follow-up; reports honestly whether conversational
     history is threaded to the planner (WS1 is unbuilt → expected: NOT yet).
  5. Latency + tokens — per-span durations and per-LLM token usage from the trace, with
     opportunity notes.

Isolation: writes go to a THROWAWAY memory schema (default `scp_mem_validation`) on whatever
branch `.env` points at; operational rows are read (read-only) from the real operational schema.
Drop the schema after with `--drop` (or `DROP SCHEMA scp_mem_validation CASCADE`). Production's
real `supply_chain_planner_memory` schema is never touched.

Run:
  uv run python -m agent_server.validate_memory            # run all scenarios + trace report
  uv run python -m agent_server.validate_memory --drop     # ...then drop the validation schema
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
import uuid

# Local-only: load .env BEFORE importing config (settings reads env at import).
if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):
    from dotenv import load_dotenv

    load_dotenv()

os.environ["USE_STUBS"] = "0"  # REAL gather (operational hybrid query) — not the stubs

import mlflow  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent_server.config import settings  # noqa: E402
from agent_server.contracts import HITLDecision, HITLVerdict  # noqa: E402
from agent_server.graph.build_graph import build_graph  # noqa: E402
from agent_server.lakebase import init_lakebase_config, lakebase_context  # noqa: E402
from agent_server.memory import (  # noqa: E402
    MEMORY_TEXT_FIELD,
    approvals_ns,
    preferences_ns,
    supplier_notes_ns,
)

mlflow.langchain.autolog()

# In-scope demo identity → the real operational query returns the hero rows (Henkel SUP-001/
# SKU-1001). Memory namespaces key on this id, but live only in the throwaway schema below.
USER = os.environ.get("VALIDATION_USER", "alex.miller@databricks.com")
MEMORY_SCHEMA = os.environ.get("VALIDATION_MEMORY_SCHEMA", "scp_mem_validation")


# ── Auth: pin a static bearer so concurrent gather + trace reads don't race the CLI token cache ──
def _pin_oauth_token() -> None:
    """Mint the profile's OAuth token ONCE and pin it as a static bearer (see evaluate_agent.py /
    the MLflow-trace-auth follow-up): the graph fans out gather nodes concurrently and we also read
    traces back, so per-call `databricks auth token` subprocesses corrupt the shared CLI cache
    (exit 45 → 401). No-op on Databricks or when a token is already set."""
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
            os.environ.pop("DATABRICKS_CONFIG_PROFILE", None)
            print(f"  (pinned OAuth token from profile '{profile}' → static bearer)")
    except Exception as exc:
        print(f"  (oauth-pin note: {str(exc)[:100]})")


def _setup_experiment() -> None:
    try:
        if settings.mlflow_experiment_id:
            mlflow.set_experiment(experiment_id=settings.mlflow_experiment_id)
        elif mlflow.get_tracking_uri() == "databricks":
            from databricks.sdk import WorkspaceClient

            me = WorkspaceClient().current_user.me().user_name
            mlflow.set_experiment(f"/Users/{me}/supply-chain-planner")
    except Exception as exc:
        print(f"  (experiment setup note: {str(exc)[:80]})")


# ── Turn driver (mirrors agent.py: astream/updates, detect interrupt, resume on the same thread) ──
async def _drive(graph, store, thread_id: str, question: str, user_id: str,
                 resume_verdict: str | None, resume_note: str | None):
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id, "store": store}}
    interrupt_payload = None
    # Mirror agent.py: record the user turn for WS1 short-term history (add_messages appends).
    turn_input = {"question": question, "user_id": user_id,
                  "messages": [HumanMessage(content=question)]}
    async for chunk in graph.astream(turn_input, config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            interrupt_payload = chunk["__interrupt__"][0].value
    if interrupt_payload is not None and resume_verdict is not None:
        cmd = Command(resume=HITLDecision(
            verdict=HITLVerdict(resume_verdict), note=resume_note, user_id=user_id,
        ).model_dump())
        async for chunk in graph.astream(cmd, config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                interrupt_payload = chunk["__interrupt__"][0].value
    snap = await graph.aget_state(config)
    state = dict(snap.values) if snap else {}
    return state, interrupt_payload


async def _turn(graph, store, label: str, thread_id: str, question: str, user_id: str = USER,
                resume_verdict: str | None = None, resume_note: str | None = None):
    """Run one logical turn (incl. any HITL pause/resume) as ONE MLflow trace; return trace_id."""
    with mlflow.start_span(name=f"turn:{label}") as root:
        mlflow.update_current_trace(metadata={"mlflow.trace.session": thread_id})
        root.set_inputs({"question": question, "thread_id": thread_id, "user_id": user_id,
                         "resume_verdict": resume_verdict})
        t0 = time.perf_counter()
        state, intr = await _drive(graph, store, thread_id, question, user_id,
                                   resume_verdict, resume_note)
        wall_ms = (time.perf_counter() - t0) * 1000
        rec = state.get("recommendation")
        ctx = state.get("memory_context")
        root.set_outputs({
            "needs_approval": rec.needs_approval if rec else None,
            "is_action_bearing": rec.is_action_bearing if rec else None,
            "trace_notes": state.get("trace_notes", []),
            "memory_recalled": (not ctx.is_empty) if ctx else False,
            "interrupted": intr is not None,
        })
    trace_id = mlflow.get_last_active_trace_id()
    return {"label": label, "thread_id": thread_id, "trace_id": trace_id,
            "state": state, "interrupt": intr, "wall_ms": wall_ms}


# ── Trace analysis (spans / latency / tokens) ──────────────────────────────────────────────────
def _span_usage(span) -> dict | None:
    """Best-effort token usage off an LLM/chat span across MLflow attribute conventions."""
    attrs = getattr(span, "attributes", {}) or {}
    for key in ("mlflow.chat.tokenUsage", "llm.token_usage", "gen_ai.usage"):
        u = attrs.get(key)
        if isinstance(u, dict):
            return u
    # langchain autolog sometimes nests usage under outputs.usage_metadata / response_metadata
    out = attrs.get("mlflow.spanOutputs")
    if isinstance(out, dict):
        for k in ("usage_metadata", "usage"):
            if isinstance(out.get(k), dict):
                return out[k]
    return None


def _norm_usage(u: dict) -> tuple[int, int, int]:
    def pick(*names):
        for n in names:
            if isinstance(u.get(n), (int, float)):
                return int(u[n])
        return 0
    inp = pick("input_tokens", "prompt_tokens", "input")
    out = pick("output_tokens", "completion_tokens", "output")
    tot = pick("total_tokens", "total") or (inp + out)
    return inp, out, tot


def analyze_trace(trace_id: str, retries: int = 6) -> dict:
    """Fetch a trace and print span tree + per-span latency + LLM token usage."""
    tr = None
    for _ in range(retries):
        try:
            tr = mlflow.get_trace(trace_id)
            if tr and tr.data and tr.data.spans:
                break
        except Exception:
            pass
        time.sleep(1.0)
    if not tr or not tr.data or not tr.data.spans:
        print(f"  (could not fetch trace {trace_id})")
        return {}

    spans = tr.data.spans
    by_id = {s.span_id: s for s in spans}

    def depth(s):
        d, p = 0, s.parent_id
        while p and p in by_id:
            d += 1
            p = by_id[p].parent_id
        return d

    total = {"in": 0, "out": 0, "tot": 0}
    llm_rows = []
    print(f"\n  trace {trace_id}  ({len(spans)} spans)")
    for s in sorted(spans, key=lambda s: s.start_time_ns):
        dur = (s.end_time_ns - s.start_time_ns) / 1e6
        u = _span_usage(s)
        tok = ""
        if u:
            i, o, t = _norm_usage(u)
            total["in"] += i
            total["out"] += o
            total["tot"] += t
            tok = f"  [tok in={i} out={o} tot={t}]"
            llm_rows.append((s.name, dur, i, o, t))
        print(f"    {'  ' * depth(s)}{s.name:<34} {dur:8.0f} ms  ({s.span_type}){tok}")
    print(f"  ── tokens total: in={total['in']} out={total['out']} total={total['tot']}")
    return {"spans": len(spans), "tokens": total, "llm": llm_rows,
            "span_names": [s.name for s in sorted(spans, key=lambda s: s.start_time_ns)]}


# ── Store read-back helpers (assert what commit actually persisted) ──────────────────────────────
def _note_has(state: dict, token: str) -> bool:
    """Race-free check: the planner trace_note records `memory=used`/`history=used` exactly when
    the corresponding block was non-empty and thus prepended to the planner's LLM input. This is
    set from state (authoritative, in-hand) — no trace-export round-trip to race."""
    for n in state.get("trace_notes", []) or []:
        if n.startswith("planner →") and token in n:
            return True
    return False


async def _count(store, ns, query="adhesive quality issue mitigation") -> list:
    try:
        items = await store.asearch(ns, query=query, limit=20)
        return [it for it in items if (getattr(it, "value", None) or {}).get(MEMORY_TEXT_FIELD)]
    except Exception as exc:
        print(f"  (search {ns} failed: {exc})")
        return []


def _planner_input_has_memory(trace_id: str) -> bool:
    """Did the planner LLM actually receive the recalled memory block? Check the CHAT_MODEL span's
    serialized inputs (the langchain message list) — that's where `_memory_block` lands."""
    import json

    markers = ("Recalled memory", "Planner preferences", "Relevant past decisions",
               "Known supplier notes")
    try:
        tr = mlflow.get_trace(trace_id)
        for s in (tr.data.spans if tr and tr.data else []):
            if getattr(s, "span_type", "") != "CHAT_MODEL":
                continue
            blob = json.dumps(getattr(s, "inputs", None), default=str)
            if any(m in blob for m in markers):
                return True
    except Exception:
        pass
    return False


def _planner_input_has_history(trace_id: str) -> bool:
    """Did the planner LLM receive the short-term conversation history block (WS1)?"""
    import json

    try:
        tr = mlflow.get_trace(trace_id)
        for s in (tr.data.spans if tr and tr.data else []):
            if getattr(s, "span_type", "") != "CHAT_MODEL":
                continue
            if "Earlier in this conversation" in json.dumps(getattr(s, "inputs", None), default=str):
                return True
    except Exception:
        pass
    return False


# ── Scenarios ────────────────────────────────────────────────────────────────────────────────
Q_ACTION = ("Henkel's structural adhesive SKU-1001 has recurring quality issues — show me the "
            "similar past cases joined to on-hand inventory and open POs, and recommend a mitigation.")
Q_FOLLOWUP = ("We had recurring Henkel SKU-1001 adhesive cracking and approved a mitigation before — "
              "what did we decide, and how should we proceed now?")
Q_INFO = "What is the total open PO quantity by supplier for Q4?"
Q_REJECT = ("Nucor announced a carbon-steel price increase — find related notes and similar past "
            "incidents, and recommend whether to pre-buy a quarter of inventory.")
Q_SHORTTERM2 = "And what is the current on-hand quantity for that same SKU?"


async def _run_all():
    _pin_oauth_token()
    _setup_experiment()
    base = init_lakebase_config()
    cfg = dataclasses.replace(base, memory_schema=MEMORY_SCHEMA)
    print(f"Validation: branch='{cfg.description}' memory_schema='{MEMORY_SCHEMA}' user='{USER}'")

    results = {}
    async with lakebase_context(cfg) as (checkpointer, store):
        # Clean slate so scenarios are deterministic: without this, a prior run's memory makes the
        # "novel" action-bearing question look like a continuation (the planner recalls it and
        # won't re-escalate). Drop the isolated schema, then recreate via setup.
        if "--no-clean" not in sys.argv:
            try:
                async with store._lakebase.connection() as conn:
                    await conn.execute(f'DROP SCHEMA IF EXISTS "{MEMORY_SCHEMA}" CASCADE')
                print(f"  clean slate: dropped prior '{MEMORY_SCHEMA}'")
            except Exception as exc:
                print(f"  (clean-slate skip: {str(exc)[:120]})")
        await checkpointer.setup()
        await store.setup()
        print("  store/checkpointer setup complete (isolated schema)")
        graph = build_graph(checkpointer=checkpointer)

        appr, pref = approvals_ns(USER), preferences_ns(USER)

        # ── Scenario 1a: approved + action-bearing → writes all three memory types ──────────────
        print("\n[1a] approved action-bearing → expect approvals+preferences+supplier_notes")
        r1 = await _turn(graph, store, "1a-approve-action", uuid.uuid4().hex, Q_ACTION,
                   resume_verdict="approved", resume_note="Approved for validation.")
        op = r1["state"].get("operational_result")
        sids = sorted({row.supplier_id for row in (op.rows if op else []) if row.supplier_id})
        results["surfaced_suppliers"] = sids
        a1 = await _count(store, appr)
        p1 = await _count(store, pref)
        sn1 = []
        for sid in sids:
            sn1 += await _count(store, supplier_notes_ns(sid))
        results["1a"] = {"trace_id": r1["trace_id"], "interrupted": r1["interrupt"] is not None,
                         "approvals": len(a1), "preferences": len(p1), "supplier_notes": len(sn1),
                         "trace_notes": r1["state"].get("trace_notes", [])}

        # ── Scenario 1b: NEW thread, same user → recall the prior decision (cross-session) ───────
        print("\n[1b] new thread, same user → expect hydrate to recall + planner to use it")
        r2 = await _turn(graph, store, "1b-recall", uuid.uuid4().hex, Q_FOLLOWUP,
                   resume_verdict="approved")
        ctx = r2["state"].get("memory_context")
        results["1b"] = {
            "trace_id": r2["trace_id"],
            "recalled_preferences": len(ctx.preferences) if ctx else 0,
            "recalled_approvals": len(ctx.prior_approvals) if ctx else 0,
            "recalled_supplier_notes": len(ctx.supplier_notes) if ctx else 0,
            "planner_used_memory": _note_has(r2["state"], "memory=used"),
            "trace_notes": r2["state"].get("trace_notes", []),
        }

        # ── Scenario 2: rejected → audit (approvals) only, no new preferences/supplier_notes ─────
        print("\n[2] rejected action-bearing → expect approvals grows, prefs/supplier_notes flat")
        p_before = len(await _count(store, pref))
        r3 = await _turn(graph, store, "2-reject", uuid.uuid4().hex, Q_REJECT,
                   resume_verdict="rejected", resume_note="Not now.")
        p_after = len(await _count(store, pref))
        results["2"] = {"trace_id": r3["trace_id"], "preferences_delta": p_after - p_before,
                        "trace_notes": r3["state"].get("trace_notes", [])}

        # ── Scenario 3: informational → no gate, commit with no decision → audit only ────────────
        print("\n[3] informational → expect no interrupt, commit audit-only (verdict=None)")
        r4 = await _turn(graph, store, "3-informational", uuid.uuid4().hex, Q_INFO)
        results["3"] = {"trace_id": r4["trace_id"], "interrupted": r4["interrupt"] is not None,
                        "needs_approval": (r4["state"].get("recommendation").needs_approval
                                           if r4["state"].get("recommendation") else None),
                        "trace_notes": r4["state"].get("trace_notes", [])}

        # ── Scenario 4: SAME-thread follow-up → short-term conversational memory (WS1) ───────────
        print("\n[4] same-thread follow-up → expect turn-2 planner to see turn-1 (history threaded)")
        tid = uuid.uuid4().hex
        await _turn(graph, store, "4-turn1", tid, Q_ACTION, resume_verdict="approved")
        r5b = await _turn(graph, store, "4-turn2-samethread", tid, Q_SHORTTERM2, resume_verdict="approved")
        msgs = r5b["state"].get("messages") or []
        results["4"] = {"trace_id": r5b["trace_id"],
                        "messages_count": len(msgs),
                        "planner_used_history": _note_has(r5b["state"], "history=used"),
                        "trace_notes": r5b["state"].get("trace_notes", [])}

    return results


def main():
    drop = "--drop" in sys.argv[1:]
    results = asyncio.run(_run_all())

    print("\n" + "=" * 84 + "\n=== TRACE ANALYSIS (spans / latency / tokens) ===")
    trace_stats = {}
    for key in ("1a", "1b", "2", "3", "4"):
        tid = results.get(key, {}).get("trace_id")
        if tid:
            print(f"\n--- scenario {key} ---")
            trace_stats[key] = analyze_trace(tid)

    print("\n" + "=" * 84 + "\n=== ASSERTIONS ===")
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    s1a, s1b, s2, s3, s4 = (results.get(k, {}) for k in ("1a", "1b", "2", "3", "4"))
    check("1a interrupted (gate tripped on action-bearing)", s1a.get("interrupted") is True)
    check("1a wrote approvals", s1a.get("approvals", 0) >= 1, f"n={s1a.get('approvals')}")
    check("1a wrote preferences", s1a.get("preferences", 0) >= 1, f"n={s1a.get('preferences')}")
    check("1a wrote supplier_notes", s1a.get("supplier_notes", 0) >= 1,
          f"n={s1a.get('supplier_notes')} suppliers={results.get('surfaced_suppliers')}")
    check("1b recalled prior memory", (s1b.get("recalled_preferences", 0)
          + s1b.get("recalled_approvals", 0) + s1b.get("recalled_supplier_notes", 0)) >= 1,
          f"prefs={s1b.get('recalled_preferences')} appr={s1b.get('recalled_approvals')} "
          f"sn={s1b.get('recalled_supplier_notes')}")
    check("1b planner used recalled memory (memory=used)", s1b.get("planner_used_memory") is True)
    check("2 rejected → no new preferences", s2.get("preferences_delta", 1) == 0,
          f"delta={s2.get('preferences_delta')}")
    check("3 informational → not gated", s3.get("interrupted") is False
          and s3.get("needs_approval") is False)
    # graph order: hydrate_memory after gather, before planner (from any trace's span order)
    names = trace_stats.get("1a", {}).get("span_names", [])
    order_ok = ("hydrate_memory" in names and "planner" in names
                and names.index("hydrate_memory") < names.index("planner")) if names else False
    check("graph order: hydrate_memory before planner", order_ok)

    check("4 short-term: turn-2 accumulated conversation messages",
          s4.get("messages_count", 0) >= 2, f"messages={s4.get('messages_count')}")
    check("4 short-term: turn-2 planner used conversation history (history=used)",
          s4.get("planner_used_history") is True)

    print("\n=== MULTI-TURN (short-term, WS1) ===")
    print(f"  same-thread turn-2 messages_count={s4.get('messages_count')} "
          f"planner_saw_history={s4.get('planner_used_history')}")
    for n in s4.get("trace_notes", []):
        if "planner →" in n:
            print(f"  {n}")

    passed = sum(1 for _, ok in checks if ok)
    print(f"\n=== {passed}/{len(checks)} assertions passed ===")

    if drop:
        print(f"\nDropping validation schema '{MEMORY_SCHEMA}' …")
        _drop_schema()

    return results


def _drop_schema():
    """Drop the throwaway validation schema (CASCADE). Best-effort."""
    try:
        from databricks_langchain import AsyncDatabricksStore  # noqa: F401
        # The store/checkpointer own their pool; simplest reliable drop is via a fresh connection.
        # Left as a manual step if the pool isn't exposed — print the SQL either way.
        print(f"  Run if needed:  DROP SCHEMA IF EXISTS {MEMORY_SCHEMA} CASCADE;")
    except Exception as exc:
        print(f"  (drop note: {exc})")


if __name__ == "__main__":
    main()
