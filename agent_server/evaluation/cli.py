"""CLI + orchestration for the eval flywheel.

`agent-evaluate` entry point. Three modes:

- **bare** `agent-evaluate [N]`                  → legacy `evaluate_direct` (trace-free, stubs).
- **`--harness`** `agent-evaluate --harness`     → legacy `evaluate` (mlflow.genai.evaluate, stubs).
- **flywheel** (any of `--layer/--fast/--full`)  → `run_flywheel`: run each question (graph or
  server layer), score quality (judges + gate) + trace shape (span/latency/token), diff a baseline,
  and write eval_report.md + eval_results.json.

  Flags: `--layer graph|server` (default server) · `--fast` (USE_STUBS, deterministic scorers only)
  / `--full` (default; real endpoints + LLM judges) · `--limit N` · `--base-url URL` ·
  `--update-baseline`.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import mlflow

from agent_server.config import settings
from agent_server.evaluation.baseline import compare, load_baseline, save_baseline
from agent_server.evaluation.dataset import EVAL_RECORDS
from agent_server.evaluation.report import aggregate, failing_rows, write_report, write_results_json
from agent_server.evaluation.runners import (
    DEFAULT_BASE_URL,
    _pin_oauth_token,
    _setup_experiment,
    fetch_trace,
    fetch_trace_by_session,
    run_agent,
    run_via_server,
    summarize_trace,
)
from agent_server.evaluation.scorers import (
    _deterministic_scorers,
    _gate_correct,
    _scorers,
    score_per_span_latency,
    score_span_structure,
    score_token_budget,
)


# ── Legacy paths (moved verbatim from evaluate_agent.py) ────────────────────────────────────────

def evaluate(limit: int | None = None):
    """Full MLflow harness path (mlflow.genai.evaluate). Persists an evaluation run + assessments
    to the experiment — works when auth permits trace/assessment writes (ambient SP on Databricks,
    or a PAT locally). On a local U2M-OAuth profile the workspace rejects these APIs; use
    evaluate_direct() instead for in-process scores."""
    os.environ.setdefault("USE_STUBS", "1")  # deterministic gather agents for the legacy path
    _setup_experiment()
    _pin_oauth_token()  # concurrency-safe OAuth (avoids CLI token-cache race → judge 401s)
    scorers = _scorers() + _deterministic_scorers()
    for s in scorers:
        try:
            s.register()
        except Exception as exc:  # already registered / idempotent
            print(f"  (scorer {s.name} register note: {str(exc)[:80]})")
    records = EVAL_RECORDS[:limit] if limit else EVAL_RECORDS
    print(f"Evaluating {len(records)} questions with {len(scorers)} scorers...")
    results = mlflow.genai.evaluate(data=records, predict_fn=run_agent, scorers=scorers)
    print("\n=== metrics ===")
    for k, v in (results.metrics or {}).items():
        print(f"  {k}: {v}")
    return results


def _feedback_value(fb) -> str:
    return str(getattr(fb, "value", fb)).strip().lower()


def evaluate_direct(limit: int | None = None):
    """Trace-free eval: run the agent, then call each make_judge scorer directly on the
    inputs/outputs (no trace round-trip, so no artifact-credential dependency). Prints per-question
    verdicts + per-scorer pass rates. Uses the same MLflow scorers as evaluate()."""
    os.environ.setdefault("USE_STUBS", "1")
    _setup_experiment()
    _pin_oauth_token()  # concurrency-safe OAuth (avoids CLI token-cache race → judge 401s)
    scorers = _scorers()
    records = EVAL_RECORDS[:limit] if limit else EVAL_RECORDS
    tallies = {s.name: [0, 0] for s in scorers}
    tallies["gate_correctness"] = [0, 0]

    print(f"\nDirect eval — {len(records)} questions × {len(scorers) + 1} scorers\n" + "=" * 78)
    for rec in records:
        query = rec["inputs"]["query"]
        out = run_agent(query)
        print(f"\nQ: {query[:90]}")
        print(f"   route={out['route']} need_approval={out['needs_approval']} "
              f"action_bearing={out['is_action_bearing']} "
              f"(expected route~{rec['expectations']['expected_route']}, "
              f"action_bearing~{rec['expectations'].get('expected_action_bearing')})")
        ok, rationale = _gate_correct(out, rec["expectations"].get("expected_action_bearing"))
        tallies["gate_correctness"][0] += int(ok)
        tallies["gate_correctness"][1] += 1
        print(f"   - gate_correctness: {'yes' if ok else 'no'}  — {rationale[:90]}")
        for s in scorers:
            try:
                fb = s(inputs=query, outputs=out)
                val = _feedback_value(fb)
                tallies[s.name][0] += int(val == "yes")
                tallies[s.name][1] += 1
                print(f"   - {s.name}: {val}  — {(getattr(fb, 'rationale', '') or '')[:90]}")
            except Exception as exc:
                print(f"   - {s.name}: ERROR {str(exc)[:80]}")

    print("\n" + "=" * 78 + "\n=== pass rates ===")
    for name, (yes, total) in tallies.items():
        pct = f"{100*yes/total:.0f}%" if total else "n/a"
        kind = "deterministic" if name == "gate_correctness" else "LLM judge"
        print(f"  {name}: {yes}/{total} = {pct}  ({kind})")
    return tallies


# ── Flywheel ─────────────────────────────────────────────────────────────────────────────────

def _git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def _out_dir() -> Path:
    return Path(os.environ.get("FLYWHEEL_OUT_DIR", os.getcwd()))


def _log_feedback_safe(trace_id: str | None, name: str, value: str, rationale: str | None) -> None:
    """Attach one scorer verdict to its trace as an MLflow assessment so it's visible in the UI.

    Uses `mlflow.log_feedback` (writes by trace id) — NOT the trace-artifact download path that
    401s under a local U2M-OAuth profile — so judge assessments land locally too, unlike the
    `mlflow.genai.evaluate` harness path. Best-effort: a write failure must not fail the eval."""
    if not trace_id:
        return
    try:
        mlflow.log_feedback(trace_id=trace_id, name=name, value=value,
                            rationale=(rationale or "")[:1000])
    except Exception as exc:
        print(f"   (log_feedback {name} note: {str(exc)[:80]})")


def _predict(layer: str, query: str, base_url: str):
    """Return (decision, trace, status). Acquires the run's trace for the trace scorers."""
    if layer == "server":
        decision, trace_id, thread_id = run_via_server(query, base_url)
        status = decision.pop("status", None)
        trace = fetch_trace(trace_id) if trace_id else None
        if trace is None and thread_id:
            trace = fetch_trace_by_session(thread_id)
        return decision, trace, status
    # graph (in-process)
    decision = run_agent(query)
    trace = fetch_trace(mlflow.get_last_active_trace_id())
    return decision, trace, "completed"


def run_flywheel(layer: str = "server", mode: str = "full", limit: int | None = None,
                 base_url: str = DEFAULT_BASE_URL, update_baseline: bool = False,
                 real_data: bool = False):
    """Two independent axes:
      - mode (`fast`|`full`): `full` runs the LLM judges; `fast` skips them (deterministic + trace
        scorers only — a quick smoke).
      - data (`real_data`): the gather agents use stub data by DEFAULT — deterministic, repeatable
        scoring against the demo hero rows. `--real-data` (USE_STUBS=0) exercises the live
        operational hybrid query / Genie / Vector Search; it works but is non-deterministic (LLM/tool
        variance) and slower, so don't read a single real-data run as a regression.
    For the graph layer we set USE_STUBS here; for the server layer the *server* owns its env (the
    /flywheel command starts it with USE_STUBS unless --real-data)."""
    if layer not in ("graph", "server"):
        raise SystemExit(f"--layer must be graph|server (got {layer!r})")

    if layer == "graph":
        os.environ["USE_STUBS"] = "0" if real_data else "1"

    _setup_experiment()
    _pin_oauth_token()  # stable static-bearer auth for judges AND log_feedback/log_metric writes
    judges = _scorers() if mode == "full" else []

    records = EVAL_RECORDS[:limit] if limit else EVAL_RECORDS
    data_src = "real" if real_data else "stub"
    print(f"\nFlywheel — layer={layer} mode={mode} data={data_src} gather · {len(records)} questions · "
          f"{len(judges)} judges + gate + 3 trace scorers\n" + "=" * 78)

    # Wrap the run in an MLflow Run so an evaluation Run shows in the experiment and (graph layer)
    # the in-process autolog traces attach to it. log_feedback writes assessments by trace id, so
    # it works even for the server layer's cross-process traces. Best-effort: if the run can't be
    # opened (no tracking), fall through and still write the local report files.
    run_name = f"flywheel-{layer}-{mode}-{data_src}"
    try:
        run_ctx = mlflow.start_run(run_name=run_name)
    except Exception as exc:
        print(f"  (start_run note: {str(exc)[:80]} — proceeding without an MLflow Run)")
        run_ctx = contextlib.nullcontext()

    rows: list[dict] = []
    with run_ctx:
        for rec in records:
            query = rec["inputs"]["query"]
            exp = rec["expectations"]
            decision, trace, status = _predict(layer, query, base_url)

            judge_results: dict[str, str] = {}
            judge_fbs: dict[str, object] = {}
            for s in judges:
                try:
                    fb = s(inputs=query, outputs=decision)
                    judge_fbs[s.name] = fb
                    judge_results[s.name] = _feedback_value(fb)
                except Exception as ex:
                    judge_results[s.name] = "error"
                    print(f"   (judge {s.name} error: {str(ex)[:70]})")

            gate_ok, gate_rationale = _gate_correct(decision, exp.get("expected_action_bearing"))

            trace_scores = None
            summary = summarize_trace(trace) if trace is not None else None
            if summary is not None:
                ss_ok, ss_r = score_span_structure(summary, decision.get("route") or [])
                pl_ok, pl_r = score_per_span_latency(summary)
                tb_ok, tb_r = score_token_budget(summary)
                trace_scores = {
                    "span_structure": {"ok": ss_ok, "rationale": ss_r},
                    "per_span_latency": {"ok": pl_ok, "rationale": pl_r},
                    "token_budget": {"ok": tb_ok, "rationale": tb_r},
                }

            # Persist every verdict onto the trace as an assessment (judges + gate + trace shape).
            tid = summary.trace_id if summary else None
            for name, fb in judge_fbs.items():
                _log_feedback_safe(tid, name, _feedback_value(fb), getattr(fb, "rationale", None))
            _log_feedback_safe(tid, "gate_correctness", "yes" if gate_ok else "no", gate_rationale)
            for name, d in (trace_scores or {}).items():
                _log_feedback_safe(tid, name, "yes" if d["ok"] else "no", d["rationale"])

            rows.append({
                "query": query, "expected_route": exp.get("expected_route"),
                "decision": decision, "judges": judge_results,
                "gate": {"ok": gate_ok, "rationale": gate_rationale},
                "trace": trace_scores, "summary": summary,
                "trace_id": tid, "status": status,
            })
            lat = f"{summary.total_latency_ms:.0f}ms" if summary and summary.total_latency_ms else "n/a"
            print(f"  ✓ {query[:60]:60} route={decision.get('route')} gate={'ok' if gate_ok else 'X'} "
                  f"trace={'yes' if summary else 'no'} lat={lat}")

        metrics = aggregate(rows)

        # Log aggregate scores as Run metrics so the Run summary mirrors the report's pass rates.
        if mlflow.active_run() is not None:
            mlflow.set_tags({"flywheel.layer": layer, "flywheel.mode": mode,
                             "flywheel.gather_data": data_src})
            for name, val in (metrics.get("scores") or {}).items():
                if val is None:  # e.g. LLM judges in --fast mode (didn't run) — nothing to log
                    continue
                try:
                    mlflow.log_metric(f"{name}/mean", float(val))
                except Exception as exc:
                    print(f"   (log_metric {name} note: {str(exc)[:60]})")
            lat95 = (metrics.get("latency_ms") or {}).get("p95_total")
            if lat95 is not None:
                mlflow.log_metric("p95_total_latency_ms", float(lat95))
            avg_tok = (metrics.get("tokens") or {}).get("avg")
            if avg_tok is not None:
                mlflow.log_metric("avg_tokens", float(avg_tok))

    meta = {
        "layer": layer, "mode": mode, "n": len(rows),
        "gather_data": "real" if real_data else "stub",
        "base_url": base_url if layer == "server" else None,
        "experiment_id": settings.mlflow_experiment_id, "git_sha": _git_sha(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if update_baseline:
        save_baseline(metrics, meta)
        diff = []
        print("\n  (baseline_metrics.json updated)")
    else:
        diff = compare(metrics, load_baseline())

    out = _out_dir()
    write_report(out / "eval_report.md", meta, metrics, rows, diff)
    write_results_json(out / "eval_results.json", meta, metrics, rows, diff)

    print("\n" + "=" * 78)
    print("Scores:", {k: v for k, v in metrics["scores"].items()})
    regressed = [d["metric"] for d in diff if d["regressed"]]
    if regressed:
        print(f"⚠️  Regressions vs baseline: {regressed}  (warn-only)")
    n_fail = len(failing_rows(rows))
    print(f"Failing questions: {n_fail}/{len(rows)}  ·  report → {out/'eval_report.md'}")
    return {"metrics": metrics, "rows": rows, "diff": diff, "meta": meta}


# ── Arg parsing / dispatch ───────────────────────────────────────────────────────────────────

def _opt_value(args: list[str], name: str, default: str | None = None) -> str | None:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def main():
    args = sys.argv[1:]
    limit = None
    lv = _opt_value(args, "--limit")
    if lv and lv.isdigit():
        limit = int(lv)
    else:
        limit = next((int(a) for a in args if a.isdigit()), None)

    if "--harness" in args:
        evaluate(limit=limit)
        return

    if "--memory" in args:
        # Memory/state validation suite — multi-turn, real Lakebase store (isolated schema). Imported
        # lazily so the common quality-flywheel path doesn't pull in lakebase/langchain message deps.
        from agent_server.evaluation.memory_validation import (
            DEFAULT_MEMORY_SCHEMA,
            DEFAULT_USER,
            run_memory_validation,
        )
        run_memory_validation(
            drop="--drop" in args,
            no_clean="--no-clean" in args,
            memory_schema=_opt_value(args, "--memory-schema", DEFAULT_MEMORY_SCHEMA),
            user=_opt_value(args, "--validation-user", DEFAULT_USER),
        )
        return

    flywheel_flags = ("--layer", "--fast", "--full", "--flywheel", "--update-baseline",
                      "--base-url", "--real-data")
    if not any(f in args for f in flywheel_flags):
        evaluate_direct(limit=limit)
        return

    layer = _opt_value(args, "--layer", "server")
    mode = "fast" if "--fast" in args else "full"
    base_url = _opt_value(args, "--base-url", DEFAULT_BASE_URL)
    run_flywheel(layer=layer, mode=mode, limit=limit, base_url=base_url,
                 update_baseline="--update-baseline" in args,
                 real_data="--real-data" in args)


if __name__ == "__main__":
    main()
