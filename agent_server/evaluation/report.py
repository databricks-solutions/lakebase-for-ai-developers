"""Aggregation + the eval_report.md / eval_results.json writers.

A run produces a list of per-question `rows` (built by `cli.run_flywheel`); this module turns them
into a canonical `metrics` dict (the same shape stored in baseline_metrics.json) and renders the
human report. It also emits eval_results.json so the /flywheel command can pick failing trace ids
without parsing markdown.

Row shape (one per question):
    {
      "query": str, "expected_route": [...],
      "decision": {route, needs_approval, is_action_bearing, est_cost_usd, ...},
      "judges": {"routing_correctness": "yes"|"no"|"error", ...},
      "gate": {"ok": bool, "rationale": str},
      "trace": {"span_structure": {ok,rationale}, "per_span_latency": {...}, "token_budget": {...}} | None,
      "summary": TraceSummary | None, "trace_id": str|None, "status": str|None,
    }
"""

from __future__ import annotations

import json
from pathlib import Path

_JUDGE_NAMES = ("routing_correctness", "recommendation_grounded", "escalation_correctness")
_TRACE_NAMES = ("span_structure", "per_span_latency", "token_budget")


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _rate(yes: int, total: int) -> float | None:
    return round(yes / total, 4) if total else None


def aggregate(rows: list[dict]) -> dict:
    """Roll per-question rows into the canonical metrics dict (scores 0..1, latency ms, tokens)."""
    scores: dict[str, float | None] = {}

    # Judge + gate pass rates (ignore errors / missing).
    for name in _JUDGE_NAMES:
        vals = [r["judges"].get(name) for r in rows if r.get("judges")]
        yes = sum(1 for v in vals if v == "yes")
        tot = sum(1 for v in vals if v in ("yes", "no"))
        scores[name] = _rate(yes, tot)
    gate_vals = [r.get("gate", {}).get("ok") for r in rows if r.get("gate")]
    scores["gate_correctness"] = _rate(sum(1 for v in gate_vals if v), len(gate_vals))

    # Trace scorer pass rates (only over rows that had a trace).
    traced = [r for r in rows if r.get("trace")]
    for name in _TRACE_NAMES:
        vals = [r["trace"][name]["ok"] for r in traced if name in r["trace"]]
        scores[name] = _rate(sum(1 for v in vals if v), len(vals))

    # Latency.
    totals = [r["summary"].total_latency_ms for r in rows
              if r.get("summary") and r["summary"].total_latency_ms is not None]
    per_node: dict[str, list[float]] = {}
    for r in rows:
        if r.get("summary"):
            for node, ms in r["summary"].span_latency_ms.items():
                per_node.setdefault(node, []).append(ms)
    latency_ms = {
        "p50_total": _percentile(totals, 0.50),
        "p95_total": _percentile(totals, 0.95),
        "per_node_p95": {n: _percentile(v, 0.95) for n, v in sorted(per_node.items())},
    }

    # Tokens (None if no row reported usage).
    tok = [r["summary"].total_tokens for r in rows
           if r.get("summary") and r["summary"].total_tokens is not None]
    tokens = {"total": sum(tok) if tok else None,
              "avg": round(sum(tok) / len(tok), 1) if tok else None}

    return {"scores": scores, "latency_ms": latency_ms, "tokens": tokens, "n": len(rows)}


def failing_rows(rows: list[dict]) -> list[dict]:
    """Rows where any scorer failed (or a judge errored) — the candidates for trace analysis."""
    out = []
    for r in rows:
        judge_fail = any(v not in ("yes", None) for v in (r.get("judges") or {}).values())
        gate_fail = r.get("gate") and not r["gate"].get("ok")
        trace_fail = r.get("trace") and any(not v.get("ok") for v in r["trace"].values())
        if judge_fail or gate_fail or trace_fail:
            out.append(r)
    return out


def _fmt(v, suffix="") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.0f}{suffix}" if suffix == "ms" else f"{v:.2f}{suffix}"
    return f"{v}{suffix}"


def _mark(ok: bool | None) -> str:
    return "✅" if ok else ("—" if ok is None else "❌")


def write_results_json(path: Path, meta: dict, metrics: dict, rows: list[dict],
                       baseline_diff: list[dict]) -> None:
    payload = {
        "meta": meta,
        "metrics": metrics,
        "baseline_diff": baseline_diff,
        "failing_trace_ids": [r["trace_id"] for r in failing_rows(rows) if r.get("trace_id")],
        "rows": [
            {
                "query": r["query"], "expected_route": r.get("expected_route"),
                "route": r.get("decision", {}).get("route"),
                "needs_approval": r.get("decision", {}).get("needs_approval"),
                "is_action_bearing": r.get("decision", {}).get("is_action_bearing"),
                "judges": r.get("judges"), "gate": r.get("gate"),
                "trace": r.get("trace"), "trace_id": r.get("trace_id"), "status": r.get("status"),
            }
            for r in rows
        ],
    }
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def write_report(path: Path, meta: dict, metrics: dict, rows: list[dict],
                 baseline_diff: list[dict], failure_analysis: dict | None = None) -> None:
    """Render eval_report.md. `failure_analysis` maps trace_id → analyze-mlflow-trace summary, filled
    in by the /flywheel command after it analyzes failing traces."""
    L: list[str] = []
    L.append("# Agent Evaluation Flywheel — Report\n")
    L.append("## Run\n")
    for k in ("layer", "mode", "n", "base_url", "experiment_id", "git_sha", "timestamp"):
        if meta.get(k) is not None:
            L.append(f"- **{k}**: {meta[k]}")
    L.append("")

    # Aggregate scores.
    L.append("## Scores (pass rate)\n")
    L.append("| scorer | kind | pass rate |")
    L.append("|---|---|---|")
    kinds = {**{n: "LLM judge" for n in _JUDGE_NAMES},
             "gate_correctness": "deterministic",
             **{n: "trace (deterministic)" for n in _TRACE_NAMES}}
    for name, val in metrics["scores"].items():
        L.append(f"| {name} | {kinds.get(name, '')} | {_fmt(val)} |")
    L.append("")

    # Latency + tokens.
    lat = metrics["latency_ms"]
    L.append("## Latency & tokens\n")
    L.append(f"- end-to-end p50 / p95: **{_fmt(lat['p50_total'],'ms')} / {_fmt(lat['p95_total'],'ms')}**")
    if lat["per_node_p95"]:
        slow = sorted(lat["per_node_p95"].items(), key=lambda kv: -(kv[1] or 0))
        L.append("- per-node p95: " + ", ".join(f"{n}={_fmt(ms,'ms')}" for n, ms in slow))
    tk = metrics["tokens"]
    L.append(f"- tokens total / avg: **{_fmt(tk['total'])} / {_fmt(tk['avg'])}**"
             + ("" if tk["total"] is not None else "  _(endpoint reported no usage)_"))
    L.append("")

    # Per-question table.
    L.append("## Per-question\n")
    L.append("| # | route (exp) | approval / action | route | grounded | escal | gate | spans | latency | slowest node | tokens | trace |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        d = r.get("decision", {})
        j = r.get("judges", {})
        tr = r.get("trace") or {}
        s = r.get("summary")
        route = ",".join(d.get("route") or []) or "—"
        exp = ",".join(r.get("expected_route") or []) or "—"
        appr = f"{d.get('needs_approval')}/{d.get('is_action_bearing')}"
        slowest = "—"
        if s and s.span_latency_ms:
            n, ms = max(s.span_latency_ms.items(), key=lambda kv: kv[1])
            slowest = f"{n} {ms:.0f}ms"
        toks = _fmt(s.total_tokens) if s else "—"
        tid = (r.get("trace_id") or "")[:12] or "—"
        L.append(
            f"| {i} | {route} ({exp}) | {appr} | {_judge_mark(j.get('routing_correctness'))} "
            f"| {_judge_mark(j.get('recommendation_grounded'))} | {_judge_mark(j.get('escalation_correctness'))} "
            f"| {_mark(r.get('gate', {}).get('ok'))} | {_mark(tr.get('span_structure', {}).get('ok') if tr else None)} "
            f"| {_mark(tr.get('per_span_latency', {}).get('ok') if tr else None)} | {slowest} "
            f"| {toks} | `{tid}` |"
        )
    L.append("")

    # Baseline diff.
    L.append("## Baseline diff\n")
    if not baseline_diff:
        L.append("_No baseline to compare against (run with `--update-baseline` to set one)._\n")
    else:
        L.append("| metric | baseline | current | delta | |")
        L.append("|---|---|---|---|---|")
        for d in baseline_diff:
            arrow = "🔻 REGRESSED" if d["regressed"] else ("▲" if d["delta"] > 0 else "▼" if d["delta"] < 0 else "·")
            L.append(f"| {d['metric']} | {_fmt(d['baseline'])} | {_fmt(d['current'])} | {d['delta']:+.3f} | {arrow} |")
        regressed = [d["metric"] for d in baseline_diff if d["regressed"]]
        L.append("")
        L.append(f"**{'⚠️ Regressions: ' + ', '.join(regressed) if regressed else '✅ No regressions.'}** "
                 "(warn-only — the run does not fail)")
    L.append("")

    # Failures.
    fails = failing_rows(rows)
    L.append("## Failures\n")
    if not fails:
        L.append("_None — every scorer passed._\n")
    else:
        for r in fails:
            L.append(f"### Q: {r['query'][:100]}")
            L.append(f"- trace: `{r.get('trace_id') or '—'}`  ·  status: {r.get('status') or '—'}")
            for name in _JUDGE_NAMES:
                if r.get("judges", {}).get(name) not in ("yes", None):
                    L.append(f"- {name}: **{r['judges'][name]}**")
            if r.get("gate") and not r["gate"]["ok"]:
                L.append(f"- gate_correctness: ❌ — {r['gate']['rationale']}")
            for name, v in (r.get("trace") or {}).items():
                if not v.get("ok"):
                    L.append(f"- {name}: ❌ — {v['rationale']}")
            if failure_analysis and r.get("trace_id") in failure_analysis:
                L.append(f"\n  **trace analysis:** {failure_analysis[r['trace_id']]}")
            L.append("")
    path.write_text("\n".join(L) + "\n")


def _judge_mark(v) -> str:
    return {"yes": "✅", "no": "❌", "error": "⚠️"}.get(v, "—")


__all__ = ["aggregate", "failing_rows", "write_report", "write_results_json"]
