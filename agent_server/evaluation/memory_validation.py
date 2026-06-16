"""Memory + state validation suite — the deep Lakebase test the quality flywheel can't do.

The quality flywheel (`cli.run_flywheel`) is single-turn and (graph layer) wires `MemorySaver()`
with **no store**, so it never exercises long-term memory. This suite drives the REAL graph against
a REAL Lakebase store + checkpointer and inspects the resulting MLflow traces (spans, latency,
tokens). It's a separate *suite* (multi-turn, stateful) from the quality flywheel, but shares its
infra (`_pin_oauth_token`, `_setup_experiment`, `fetch_trace` from `runners`).

Validates:
  1. Write policy (commit) — approved+action-bearing ⇒ approvals+preferences+supplier_notes;
     rejected ⇒ audit (approvals) only; informational (no gate) ⇒ audit only; every value carries
     a curated `memory_text`.
  2. Recall (hydrate) — a NEW thread for the SAME user recalls the prior decision; the planner's
     LLM input actually contains the rendered memory block (cross-session "multi-turn").
  3. Graph order — `hydrate_memory` runs AFTER the gather fan-in and BEFORE `planner`.
  4. Short-term multi-turn — same-thread follow-up; reports whether conversational history is
     threaded to the planner.
  5. Latency + tokens — per-span durations and per-LLM token usage from the trace.

Isolation: writes go to a THROWAWAY memory schema (default `scp_mem_validation`) on whatever branch
`.env` points at; operational rows are read (read-only) from the real operational schema. Production's
`supply_chain_planner_memory` schema is never touched.

Run:
  uv run agent-evaluate --memory             # run all scenarios + trace report
  uv run agent-evaluate --memory --drop      # ...then print the drop-schema SQL
  uv run python -m agent_server.validate_memory   # back-compat shim → same entry
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import mlflow
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agent_server.config import settings
from agent_server.contracts import HITLDecision, HITLVerdict
from agent_server.evaluation.report import _fmt, _mark
from agent_server.evaluation.runners import _pin_oauth_token, _setup_experiment, fetch_trace
from agent_server.graph.build_graph import build_graph
from agent_server.lakebase import init_lakebase_config, lakebase_context
from agent_server.memory import (
    MEMORY_TEXT_FIELD,
    approvals_ns,
    preferences_ns,
    supplier_notes_ns,
)

# In-scope demo identity → the real operational query returns the hero rows (Henkel SUP-001/
# SKU-1001). Memory namespaces key on this id, but live only in the throwaway schema below.
DEFAULT_USER = os.environ.get("VALIDATION_USER", "demo-user@databricks.com")
DEFAULT_MEMORY_SCHEMA = os.environ.get("VALIDATION_MEMORY_SCHEMA", "scp_mem_validation")


# ── Turn driver (mirrors agent.py: astream/updates, detect interrupt, resume on the same thread) ──
async def _drive(graph, store, thread_id: str, question: str, user_id: str,
                 resume_verdict: str | None, resume_note: str | None):
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id, "store": store}}
    interrupt_payload = None
    # Mirror agent.py: record the user turn for short-term history (add_messages appends).
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


async def _turn(graph, store, label: str, thread_id: str, question: str, user_id: str = DEFAULT_USER,
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


def analyze_trace(trace_id: str) -> dict:
    """Fetch a trace and print span tree + per-span latency + LLM token usage."""
    tr = fetch_trace(trace_id)
    if not tr or not getattr(tr, "data", None) or not tr.data.spans:
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
    total_ms = float(getattr(tr.info, "execution_time_ms", 0) or 0)
    return {"spans": len(spans), "tokens": total, "llm": llm_rows, "total_ms": total_ms,
            "span_names": [s.name for s in sorted(spans, key=lambda s: s.start_time_ns)]}


# ── Store read-back helpers (assert what commit actually persisted) ──────────────────────────────
def _note_has(state: dict, token: str) -> bool:
    """Race-free check: the planner trace_note records `memory=used`/`history=used` exactly when
    the corresponding block was non-empty and thus prepended to the planner's LLM input. Set from
    state (authoritative, in-hand) — no trace-export round-trip to race."""
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


# ── Scenarios ────────────────────────────────────────────────────────────────────────────────
Q_ACTION = ("Henkel's structural adhesive SKU-1001 has recurring quality issues — show me the "
            "similar past cases joined to on-hand inventory and open POs, and recommend a mitigation.")
Q_FOLLOWUP = ("We had recurring Henkel SKU-1001 adhesive cracking and approved a mitigation before — "
              "what did we decide, and how should we proceed now?")
Q_INFO = "What is the total open PO quantity by supplier for Q4?"
Q_REJECT = ("Nucor announced a carbon-steel price increase — find related notes and similar past "
            "incidents, and recommend whether to pre-buy a quarter of inventory.")
Q_SHORTTERM2 = "And what is the current on-hand quantity for that same SKU?"


async def _run_all(user: str, memory_schema: str, no_clean: bool):
    _pin_oauth_token()
    _setup_experiment()
    base = init_lakebase_config()
    cfg = dataclasses.replace(base, memory_schema=memory_schema)
    print(f"Validation: branch='{cfg.description}' memory_schema='{memory_schema}' user='{user}'")

    results: dict = {"user": user, "memory_schema": memory_schema, "branch": cfg.description}
    async with lakebase_context(cfg) as (checkpointer, store):
        # Clean slate so scenarios are deterministic: without this, a prior run's memory makes the
        # "novel" action-bearing question look like a continuation (the planner recalls it and won't
        # re-escalate). Drop the isolated schema, then recreate via setup.
        if not no_clean:
            try:
                async with store._lakebase.connection() as conn:
                    await conn.execute(f'DROP SCHEMA IF EXISTS "{memory_schema}" CASCADE')
                print(f"  clean slate: dropped prior '{memory_schema}'")
            except Exception as exc:
                print(f"  (clean-slate skip: {str(exc)[:120]})")
        await checkpointer.setup()
        await store.setup()
        print("  store/checkpointer setup complete (isolated schema)")
        graph = build_graph(checkpointer=checkpointer)

        appr, pref = approvals_ns(user), preferences_ns(user)

        # ── 1a: approved + action-bearing → writes all three memory types ────────────────────────
        print("\n[1a] approved action-bearing → expect approvals+preferences+supplier_notes")
        r1 = await _turn(graph, store, "1a-approve-action", uuid.uuid4().hex, Q_ACTION,
                         user_id=user, resume_verdict="approved", resume_note="Approved for validation.")
        op = r1["state"].get("operational_result")
        sids = sorted({row.supplier_id for row in (op.rows if op else []) if row.supplier_id})
        results["surfaced_suppliers"] = sids
        a1 = await _count(store, appr)
        p1 = await _count(store, pref)
        sn1 = []
        for sid in sids:
            sn1 += await _count(store, supplier_notes_ns(sid))
        results["1a"] = {"trace_id": r1["trace_id"], "wall_ms": r1["wall_ms"],
                         "interrupted": r1["interrupt"] is not None,
                         "approvals": len(a1), "preferences": len(p1), "supplier_notes": len(sn1),
                         "trace_notes": r1["state"].get("trace_notes", [])}

        # ── 1b: NEW thread, same user → recall the prior decision (cross-session) ────────────────
        print("\n[1b] new thread, same user → expect hydrate to recall + planner to use it")
        r2 = await _turn(graph, store, "1b-recall", uuid.uuid4().hex, Q_FOLLOWUP,
                         user_id=user, resume_verdict="approved")
        ctx = r2["state"].get("memory_context")
        results["1b"] = {
            "trace_id": r2["trace_id"], "wall_ms": r2["wall_ms"],
            "recalled_preferences": len(ctx.preferences) if ctx else 0,
            "recalled_approvals": len(ctx.prior_approvals) if ctx else 0,
            "recalled_supplier_notes": len(ctx.supplier_notes) if ctx else 0,
            "planner_used_memory": _note_has(r2["state"], "memory=used"),
            "trace_notes": r2["state"].get("trace_notes", []),
        }

        # ── 2: rejected → audit (approvals) only, no new preferences/supplier_notes ──────────────
        print("\n[2] rejected action-bearing → expect approvals grows, prefs/supplier_notes flat")
        p_before = len(await _count(store, pref))
        r3 = await _turn(graph, store, "2-reject", uuid.uuid4().hex, Q_REJECT,
                         user_id=user, resume_verdict="rejected", resume_note="Not now.")
        p_after = len(await _count(store, pref))
        results["2"] = {"trace_id": r3["trace_id"], "wall_ms": r3["wall_ms"],
                        "preferences_delta": p_after - p_before,
                        "trace_notes": r3["state"].get("trace_notes", [])}

        # ── 3: informational → no gate, commit with no decision → audit only ─────────────────────
        print("\n[3] informational → expect no interrupt, commit audit-only (verdict=None)")
        r4 = await _turn(graph, store, "3-informational", uuid.uuid4().hex, Q_INFO, user_id=user)
        results["3"] = {"trace_id": r4["trace_id"], "wall_ms": r4["wall_ms"],
                        "interrupted": r4["interrupt"] is not None,
                        "needs_approval": (r4["state"].get("recommendation").needs_approval
                                           if r4["state"].get("recommendation") else None),
                        "trace_notes": r4["state"].get("trace_notes", [])}

        # ── 4: SAME-thread follow-up → short-term conversational memory ──────────────────────────
        print("\n[4] same-thread follow-up → expect turn-2 planner to see turn-1 (history threaded)")
        tid = uuid.uuid4().hex
        await _turn(graph, store, "4-turn1", tid, Q_ACTION, user_id=user, resume_verdict="approved")
        r5b = await _turn(graph, store, "4-turn2-samethread", tid, Q_SHORTTERM2, user_id=user,
                          resume_verdict="approved")
        msgs = r5b["state"].get("messages") or []
        results["4"] = {"trace_id": r5b["trace_id"], "wall_ms": r5b["wall_ms"],
                        "messages_count": len(msgs),
                        "planner_used_history": _note_has(r5b["state"], "history=used"),
                        "trace_notes": r5b["state"].get("trace_notes", [])}

    return results


# ── Assertions ─────────────────────────────────────────────────────────────────────────────────
def build_checks(results: dict, trace_stats: dict) -> list[dict]:
    s1a, s1b, s2, s3, s4 = (results.get(k, {}) for k in ("1a", "1b", "2", "3", "4"))
    checks: list[dict] = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

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
    names = trace_stats.get("1a", {}).get("span_names", [])
    order_ok = ("hydrate_memory" in names and "planner" in names
                and names.index("hydrate_memory") < names.index("planner")) if names else False
    check("graph order: hydrate_memory before planner", order_ok)
    check("4 short-term: turn-2 accumulated conversation messages",
          s4.get("messages_count", 0) >= 2, f"messages={s4.get('messages_count')}")
    check("4 short-term: turn-2 planner used conversation history (history=used)",
          s4.get("planner_used_history") is True)
    return checks


# ── Reports ──────────────────────────────────────────────────────────────────────────────────
def _out_dir() -> Path:
    return Path(os.environ.get("FLYWHEEL_OUT_DIR", os.getcwd()))


def _git_sha() -> str | None:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def write_memory_results(path: Path, meta: dict, results: dict, checks: list[dict],
                         trace_stats: dict) -> None:
    payload = {"meta": meta, "checks": checks,
               "scenarios": {k: results.get(k) for k in ("1a", "1b", "2", "3", "4")},
               "surfaced_suppliers": results.get("surfaced_suppliers"),
               "trace_stats": trace_stats}
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def write_memory_report(path: Path, meta: dict, results: dict, checks: list[dict],
                        trace_stats: dict) -> None:
    L: list[str] = ["# Memory & State Validation — Report\n", "## Run\n"]
    for k in ("user", "memory_schema", "branch", "experiment_id", "git_sha", "timestamp"):
        if meta.get(k) is not None:
            L.append(f"- **{k}**: {meta[k]}")
    passed = sum(1 for c in checks if c["ok"])
    L.append(f"\n**{passed}/{len(checks)} assertions passed**\n")

    L.append("## Assertions\n")
    L.append("| | check | detail |")
    L.append("|---|---|---|")
    for c in checks:
        L.append(f"| {_mark(c['ok'])} | {c['name']} | {c['detail']} |")
    L.append("")

    L.append("## Per-scenario trace (spans / latency / tokens)\n")
    L.append("| scenario | spans | wall | trace latency | tokens (in/out/total) | trace |")
    L.append("|---|---|---|---|---|---|")
    labels = {"1a": "approve→write", "1b": "new-thread recall", "2": "reject→audit-only",
              "3": "informational", "4": "same-thread turn-2"}
    for k in ("1a", "1b", "2", "3", "4"):
        r, ts = results.get(k, {}), trace_stats.get(k, {})
        tok = ts.get("tokens", {})
        tok_s = f"{tok.get('in','—')}/{tok.get('out','—')}/{tok.get('tot','—')}" if tok else "—"
        L.append(f"| {k} {labels[k]} | {ts.get('spans','—')} | {_fmt(r.get('wall_ms'),'ms')} "
                 f"| {_fmt(ts.get('total_ms'),'ms')} | {tok_s} | `{(r.get('trace_id') or '')[:12] or '—'}` |")
    L.append("")

    L.append("## Short-term\n")
    s4 = results.get("4", {})
    L.append(f"- same-thread turn-2 messages: **{s4.get('messages_count')}** · "
             f"planner saw history: **{s4.get('planner_used_history')}**")
    for n in s4.get("trace_notes", []):
        if "planner →" in n:
            L.append(f"  - `{n}`")
    L.append("")
    path.write_text("\n".join(L) + "\n")


# ── Entry ────────────────────────────────────────────────────────────────────────────────────
def run_memory_validation(drop: bool = False, no_clean: bool = False,
                          memory_schema: str = DEFAULT_MEMORY_SCHEMA, user: str = DEFAULT_USER):
    """Run the memory/state validation suite end-to-end: scenarios → trace analysis → assertions →
    memory_report.md + memory_results.json. Returns the structured results."""
    os.environ["USE_STUBS"] = "0"  # REAL gather (operational hybrid query) — not the stubs

    results = asyncio.run(_run_all(user, memory_schema, no_clean))

    print("\n" + "=" * 84 + "\n=== TRACE ANALYSIS (spans / latency / tokens) ===")
    trace_stats = {}
    for key in ("1a", "1b", "2", "3", "4"):
        tid = results.get(key, {}).get("trace_id")
        if tid:
            print(f"\n--- scenario {key} ---")
            trace_stats[key] = analyze_trace(tid)

    print("\n" + "=" * 84 + "\n=== ASSERTIONS ===")
    checks = build_checks(results, trace_stats)
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}  {c['detail']}")

    s4 = results.get("4", {})
    print("\n=== MULTI-TURN (short-term) ===")
    print(f"  same-thread turn-2 messages_count={s4.get('messages_count')} "
          f"planner_saw_history={s4.get('planner_used_history')}")

    passed = sum(1 for c in checks if c["ok"])
    print(f"\n=== {passed}/{len(checks)} assertions passed ===")

    meta = {"user": user, "memory_schema": memory_schema, "branch": results.get("branch"),
            "experiment_id": settings.mlflow_experiment_id, "git_sha": _git_sha(),
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out = _out_dir()
    write_memory_report(out / "memory_report.md", meta, results, checks, trace_stats)
    write_memory_results(out / "memory_results.json", meta, results, checks, trace_stats)
    print(f"report → {out/'memory_report.md'}")

    if drop:
        print(f"\nDrop the validation schema when done:  DROP SCHEMA IF EXISTS {memory_schema} CASCADE;")

    return {"results": results, "checks": checks, "trace_stats": trace_stats, "meta": meta}


def main():
    import sys

    args = sys.argv[1:]
    run_memory_validation(
        drop="--drop" in args,
        no_clean="--no-clean" in args,
        memory_schema=_arg_value(args, "--memory-schema", DEFAULT_MEMORY_SCHEMA),
        user=_arg_value(args, "--validation-user", DEFAULT_USER),
    )


def _arg_value(args: list[str], name: str, default: str) -> str:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


if __name__ == "__main__":
    main()
