"""Baseline regression tracking — warn, don't block.

A known-good metrics snapshot lives at `baseline_metrics.json` next to this module (committed). Each
run diffs its metrics against it and flags regressions, but the flywheel always exits 0 for now — the
diff is surfaced in the report so a human decides. `--update-baseline` rewrites the snapshot when the
agent legitimately changes.

Regression rules (tolerances are deliberately loose; this is a dev signal, not a gate):
  - quality scores (0..1, higher better): regressed if current < baseline - QUALITY_TOL
  - latency p95 (ms, lower better):        regressed if current > baseline * (1 + LATENCY_TOL)
  - tokens avg (lower better):             regressed if current > baseline * (1 + TOKEN_TOL)
"""

from __future__ import annotations

import json
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "baseline_metrics.json"

QUALITY_TOL = 0.05   # absolute drop in a pass rate that counts as a regression
LATENCY_TOL = 0.20   # fractional p95 latency increase that counts as a regression
TOKEN_TOL = 0.20     # fractional avg-token increase that counts as a regression


def load_baseline(path: Path = BASELINE_PATH) -> dict | None:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def save_baseline(metrics: dict, meta: dict, path: Path = BASELINE_PATH) -> None:
    path.write_text(json.dumps({"meta": meta, "metrics": metrics}, indent=2, default=str) + "\n")


def compare(current: dict, baseline: dict | None) -> list[dict]:
    """Return one diff row per comparable metric: {metric, kind, baseline, current, delta, regressed}."""
    if not baseline:
        return []
    base_metrics = baseline.get("metrics", baseline)  # tolerate either shape
    diffs: list[dict] = []

    # Quality scores (higher better).
    cur_scores = (current.get("scores") or {})
    base_scores = (base_metrics.get("scores") or {})
    for name in sorted(set(cur_scores) | set(base_scores)):
        c, b = cur_scores.get(name), base_scores.get(name)
        if c is None or b is None:
            continue
        diffs.append({"metric": name, "kind": "score", "baseline": b, "current": c,
                      "delta": c - b, "regressed": c < b - QUALITY_TOL})

    # Latency p95 end-to-end (lower better).
    c = (current.get("latency_ms") or {}).get("p95_total")
    b = (base_metrics.get("latency_ms") or {}).get("p95_total")
    if c is not None and b is not None:
        diffs.append({"metric": "p95_total_latency_ms", "kind": "latency", "baseline": b, "current": c,
                      "delta": c - b, "regressed": c > b * (1 + LATENCY_TOL)})

    # Avg tokens (lower better; may be None when the endpoint reports no usage).
    c = (current.get("tokens") or {}).get("avg")
    b = (base_metrics.get("tokens") or {}).get("avg")
    if c is not None and b is not None:
        diffs.append({"metric": "avg_tokens", "kind": "tokens", "baseline": b, "current": c,
                      "delta": c - b, "regressed": c > b * (1 + TOKEN_TOL)})

    return diffs


__all__ = ["BASELINE_PATH", "load_baseline", "save_baseline", "compare"]
