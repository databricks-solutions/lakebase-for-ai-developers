"""Scorers — output-quality judges, the deterministic gate scorer, and trace-shape scorers.

Two families:

1. **Output scorers** judge the predict_fn OUTPUT (the flattened decision dict): three LLM judges
   (`routing_correctness`, `recommendation_grounded`, `escalation_correctness`) via the Databricks
   managed judge, plus the deterministic `gate_correctness`. These are layer-agnostic and used by
   both the legacy `evaluate`/`evaluate_direct` paths and the flywheel.

2. **Trace scorers** judge the SHAPE of the run, from a `TraceSummary` that `runners.summarize_trace`
   extracts from the MLflow trace: `score_span_structure` (required nodes present),
   `score_per_span_latency` (each node under budget), `score_token_budget` (total tokens under
   budget). All deterministic (no LLM), so they're stable run-to-run and free. They're plain
   predicate functions — the flywheel calls them directly with the summary it fetched, the same way
   `evaluate_direct` calls `_gate_correct` directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from mlflow.entities import Feedback
from mlflow.genai.judges import make_judge
from mlflow.genai.scorers import scorer

# ── LLM-as-judge scorers (Databricks managed model, yes/no) ────────────────────────────────────

def _scorers():
    routing = make_judge(
        name="routing_correctness",
        instructions=(
            "You are judging a supply-chain copilot's agent routing.\n"
            "Question: {{ inputs }}\n"
            "Agent output (includes the chosen `route` of gather agents and `route_reasoning`): {{ outputs }}\n\n"
            "Gather agents: 'operational' (similar past quality issues/cases joined to live inventory/POs), "
            "'analytics' (counts/sums/rollups/totals via NL->SQL), 'knowledge' (contracts, supplier "
            "notifications, market events, competitor/promotion docs).\n"
            "Did the route select the appropriate agent(s) for what the question needs? It is acceptable "
            "to include an extra reasonable agent. Answer 'yes' or 'no'."
        ),
        model="databricks",
    )
    grounded = make_judge(
        name="recommendation_grounded",
        instructions=(
            "You are judging a supply-chain copilot's recommendation.\n"
            "Question: {{ inputs }}\n"
            "Agent output (includes `summary`, `actions`, and the `evidence` it gathered): {{ outputs }}\n\n"
            "Is the recommendation grounded in the provided evidence (no invented suppliers/SKUs/numbers) "
            "AND actionable (concrete steps a planner can execute)? Answer 'yes' or 'no'."
        ),
        model="databricks",
    )
    escalation = make_judge(
        name="escalation_correctness",
        instructions=(
            "You are judging whether a supply-chain copilot escalated correctly for human approval.\n"
            "Question: {{ inputs }}\n"
            "Agent output (includes `needs_approval`, `est_cost_usd`, `actions`): {{ outputs }}\n\n"
            "needs_approval SHOULD be true when the recommendation commits spend or is risky/irreversible "
            "(reorder, re-source, pre-buy, quarantine/hold) or the cost is high/unknown; it should be false "
            "for purely informational answers (counts, lookups, status). Is `needs_approval` set appropriately "
            "for this question and output? Answer 'yes' or 'no'."
        ),
        model="databricks",
    )
    return [routing, grounded, escalation]


# ── Deterministic gate scorer (no LLM judge → stable, repeatable) ──────────────────────────────

def _gate_correct(outputs: dict, expected_action_bearing: bool | None) -> tuple[bool, str]:
    """Compare the planner's deterministic gate signal to the labeled expectation. Scores the
    `is_action_bearing` classification (what the gate keys on) rather than the LLM judge's opinion,
    so the escalation result is repeatable run-to-run (see docs/follow-ups.md eval gaps)."""
    actual = outputs.get("is_action_bearing")
    needs_approval = outputs.get("needs_approval")
    ok = expected_action_bearing is not None and actual == expected_action_bearing
    rationale = (
        f"is_action_bearing={actual} (expected {expected_action_bearing}); "
        f"needs_approval={needs_approval}, est_cost_usd={outputs.get('est_cost_usd')}"
    )
    return ok, rationale


@scorer(name="gate_correctness")
def gate_correctness(outputs, expectations):
    """Deterministic: does is_action_bearing match the labeled expectation?"""
    ok, rationale = _gate_correct(outputs, (expectations or {}).get("expected_action_bearing"))
    return Feedback(value="yes" if ok else "no", rationale=rationale)


def _deterministic_scorers():
    return [gate_correctness]


# ── Trace shape: budgets + the normalized summary the trace scorers read ────────────────────────

# Per-node latency budgets (ms). Generous dev defaults for real Opus/Genie/VS endpoints — these are
# warn-only thresholds, not hard SLAs. Override the whole map with the FLYWHEEL_LATENCY_BUDGETS_MS
# env var (JSON object), e.g. '{"planner": 45000, "__total__": 120000}'. "__total__" bounds the
# end-to-end run; "__default__" applies to any node without an explicit budget.
_DEFAULT_LATENCY_BUDGETS_MS: dict[str, float] = {
    "supervisor": 15_000,
    "gather_knowledge": 20_000,
    "gather_analytics": 20_000,
    "gather_operational": 20_000,
    "hydrate_memory": 5_000,
    "planner": 30_000,
    "hitl_review": 2_000,
    "commit": 5_000,
    "__total__": 90_000,
    "__default__": 30_000,
}


def _load_latency_budgets() -> dict[str, float]:
    raw = os.environ.get("FLYWHEEL_LATENCY_BUDGETS_MS")
    if not raw:
        return dict(_DEFAULT_LATENCY_BUDGETS_MS)
    try:
        override = json.loads(raw)
        merged = dict(_DEFAULT_LATENCY_BUDGETS_MS)
        merged.update({k: float(v) for k, v in override.items()})
        return merged
    except Exception:
        return dict(_DEFAULT_LATENCY_BUDGETS_MS)


LATENCY_BUDGETS_MS = _load_latency_budgets()
TOKEN_BUDGET = int(os.environ.get("FLYWHEEL_TOKEN_BUDGET", "40000"))

# Spans every run must have, regardless of route (LangGraph node names from build_graph).
_ALWAYS_REQUIRED_SPANS = ("supervisor", "hydrate_memory", "planner")
# A run terminates in exactly one of these.
_TERMINAL_SPANS = ("commit", "hitl_review")


@dataclass
class TraceSummary:
    """Normalized view of one run's trace, produced by `runners.summarize_trace`. Decouples the
    trace scorers from the MLflow Trace object shape (and makes them trivially testable)."""

    trace_id: str | None = None
    span_names: set[str] = field(default_factory=set)
    span_latency_ms: dict[str, float] = field(default_factory=dict)  # node name → its span ms
    total_latency_ms: float | None = None
    total_tokens: int | None = None  # None = endpoint reported no usage (not a failure)
    state: str | None = None  # trace state: OK / ERROR / IN_PROGRESS


def _has_span(span_names: set[str], target: str) -> bool:
    """Tolerant match — LangGraph autolog usually names a node span exactly, but accept a suffix /
    substring so a wrapping span name doesn't trip the (warn-only) structure check."""
    return any(n == target or n.endswith(target) or target in n for n in span_names)


def score_span_structure(summary: TraceSummary, route: list[str]) -> tuple[bool, str]:
    """Are the spans the route implies all present? supervisor + gather_<routed> + hydrate_memory +
    planner + a terminal node (commit|hitl_review)."""
    required = list(_ALWAYS_REQUIRED_SPANS) + [f"gather_{a}" for a in (route or [])]
    missing = [s for s in required if not _has_span(summary.span_names, s)]
    has_terminal = any(_has_span(summary.span_names, t) for t in _TERMINAL_SPANS)
    if not has_terminal:
        missing.append("|".join(_TERMINAL_SPANS))
    ok = not missing
    rationale = "all required spans present" if ok else f"missing spans: {missing}"
    if summary.state and summary.state.upper() != "OK":
        ok = False
        rationale = f"trace state={summary.state}; " + rationale
    return ok, rationale


def score_per_span_latency(summary: TraceSummary) -> tuple[bool, str]:
    """Is each KNOWN node — and the end-to-end run — within budget?

    Only spans with an explicit budget in LATENCY_BUDGETS_MS (the LangGraph node names) are checked.
    Autolog wrapper/sub-spans (run_agent, LangGraph, RunnableSequence, ChatDatabricks, …) represent
    aggregate time, not a single node's work, so applying a per-node budget to them would false-
    positive on every slow run — end-to-end time is bounded by the `__total__` budget instead."""
    breaches = []
    for node, ms in summary.span_latency_ms.items():
        budget = LATENCY_BUDGETS_MS.get(node)  # explicit node budgets only — no catch-all default
        if budget is not None and ms > budget:
            breaches.append(f"{node} {ms:.0f}ms>{budget:.0f}ms")
    total_budget = LATENCY_BUDGETS_MS.get("__total__")
    if summary.total_latency_ms is not None and total_budget and summary.total_latency_ms > total_budget:
        breaches.append(f"__total__ {summary.total_latency_ms:.0f}ms>{total_budget:.0f}ms")
    ok = not breaches
    rationale = "within latency budgets" if ok else f"latency breaches: {breaches}"
    return ok, rationale


def score_token_budget(summary: TraceSummary) -> tuple[bool, str]:
    """Is total token usage within budget? Unknown usage is reported, not failed."""
    if summary.total_tokens is None:
        return True, "token usage unavailable (endpoint reported none) — not scored"
    ok = summary.total_tokens <= TOKEN_BUDGET
    rationale = f"total_tokens={summary.total_tokens} (budget={TOKEN_BUDGET})"
    return ok, rationale


# Names line up with the metric keys the report/baseline use.
TRACE_SCORER_NAMES = ("span_structure", "per_span_latency", "token_budget")


__all__ = [
    "_scorers",
    "_gate_correct",
    "gate_correctness",
    "_deterministic_scorers",
    "TraceSummary",
    "LATENCY_BUDGETS_MS",
    "TOKEN_BUDGET",
    "TRACE_SCORER_NAMES",
    "score_span_structure",
    "score_per_span_latency",
    "score_token_budget",
]
