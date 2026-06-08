"""Agent evaluation — MLflow GenAI scorers over the supervisor graph.

Evaluates the *current* pipeline (real supervisor + planner LLM, stub gather agents) on the
dimensions that don't depend on live operational rows:
  - routing_correctness     — did the supervisor pick the right gather agents?
  - recommendation_grounded — is the recommendation grounded in the gathered evidence + actionable?
  - escalation_correctness  — is needs_approval set appropriately (gate trips when it should)?

Operational SQL/join correctness is deferred until the Synced Tables are provisioned (then the
gather agents return real rows). Scorers judge the predict_fn OUTPUT (not trace spans), so they use
the Databricks managed judge server-side and need no local trace-artifact access.

Run:  uv run agent-evaluate          (or: uv run python -m agent_server.evaluate_agent)
"""

from __future__ import annotations

import asyncio
import os
import uuid

os.environ.setdefault("USE_STUBS", "1")  # deterministic gather agents for eval

import mlflow  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from mlflow.entities import Feedback  # noqa: E402
from mlflow.genai.judges import make_judge  # noqa: E402
from mlflow.genai.scorers import scorer  # noqa: E402

from agent_server.config import settings  # noqa: E402  (loads .env: profile + tracking uri)
from agent_server.graph.build_graph import build_graph  # noqa: E402

# MLflow setup for the eval process. We deliberately do NOT export a bearer token here (unlike the
# server) — the eval harness must READ each prediction's trace to feed the scorers, and the
# artifact get-credentials API rejects the U2M token type. Profile auth reads fresh traces fine.
mlflow.langchain.autolog()


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


_setup_experiment()
_GRAPH = build_graph(checkpointer=MemorySaver())

# ── Evaluation dataset (sanity set derived from the agent's purpose + demo scenarios) ──────────
# expected_route documents intent for the routing judge; not a hard label.
# `expected_action_bearing` is the DETERMINISTIC gate label (does the recommendation commit
# spend / is it risky-irreversible, vs purely informational) — scored without an LLM by
# `gate_correctness`. `should_need_approval` mirrors it (the gate is action_bearing OR cost≥thresh,
# and no sanity question clears the cost threshold). `expected_route` documents intent for the
# routing judge; not a hard label.
EVAL_RECORDS = [
    {
        # Canonical demo question — phrased to ask for a recommendation so the gate moment is
        # unambiguous (a bare "show me …" is borderline; see docs/follow-ups.md eval gaps).
        "inputs": {"query": "Henkel's structural adhesive SKU-1001 has recurring quality issues — "
                            "show me the similar past cases joined to on-hand inventory and open POs, "
                            "and recommend a mitigation."},
        "expectations": {"expected_route": ["operational"],
                         "expected_action_bearing": True,
                         "should_need_approval": True,
                         "note": "Similar-cases + live inventory/PO join → operational; mitigation is actionable/costly → approve."},
    },
    {
        "inputs": {"query": "What is the total open PO quantity by supplier for Q4?"},
        "expectations": {"expected_route": ["analytics"],
                         "expected_action_bearing": False,
                         "should_need_approval": False,
                         "note": "Pure aggregation → analytics/Genie; informational → no approval needed."},
    },
    {
        "inputs": {"query": "What do our Caterpillar and Lockheed Martin contracts say about late-delivery penalties?"},
        "expectations": {"expected_route": ["knowledge"],
                         "expected_action_bearing": False,
                         "should_need_approval": False,
                         "note": "Document lookup → knowledge/Vector Search; informational."},
    },
    {
        "inputs": {"query": "Henkel SKU-1001 has recurring adhesive cracking. Recommend a mitigation "
                            "given on-hand inventory and open POs, and total open POs by supplier for Q4."},
        "expectations": {"expected_route": ["operational", "analytics"],
                         "expected_action_bearing": True,
                         "should_need_approval": True,
                         "note": "Similar cases + aggregation; mitigation (reorder/re-source) is costly → approve."},
    },
    {
        "inputs": {"query": "Which suppliers are currently flagged at risk?"},
        "expectations": {"expected_route": ["analytics"],
                         "expected_action_bearing": False,
                         "should_need_approval": False,
                         "note": "Status rollup → analytics; informational → no approval."},
    },
    {
        "inputs": {"query": "Nucor announced a carbon-steel price increase. Find related market-event "
                            "notes and similar past incidents, and recommend whether to pre-buy."},
        "expectations": {"expected_route": ["knowledge", "operational"],
                         "expected_action_bearing": True,
                         "should_need_approval": True,
                         "note": "Docs + similar incidents; a pre-buy is a costly commitment → approve."},
    },
]


# ── predict_fn: run the current pipeline offline and return the decision to judge ──────────────
async def _arun(query: str) -> dict:
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex, "user_id": "eval@databricks.com"}}
    await _GRAPH.ainvoke({"question": query, "user_id": "eval@databricks.com"}, cfg)
    snap = await _GRAPH.aget_state(cfg)
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


@mlflow.trace
def run_agent(query: str) -> dict:
    """predict_fn for mlflow.genai.evaluate — returns the routing + recommendation decision."""
    state = asyncio.run(_arun(query))
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


# ── Scorers (LLM-as-judge, Databricks managed model, yes/no) ───────────────────────────────────
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


def evaluate(limit: int | None = None):
    """Full MLflow harness path (mlflow.genai.evaluate). Persists an evaluation run + assessments
    to the experiment — works when auth permits trace/assessment writes (ambient SP on Databricks,
    or a PAT locally). On a local U2M-OAuth profile the workspace rejects these APIs; use
    evaluate_direct() instead for in-process scores."""
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
    scorers = _scorers()
    records = EVAL_RECORDS[:limit] if limit else EVAL_RECORDS
    # gate_correctness is deterministic (no LLM); tallied separately since it needs `expectations`.
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
        # Deterministic gate scorer first (stable signal).
        ok, rationale = _gate_correct(out, rec["expectations"].get("expected_action_bearing"))
        tallies["gate_correctness"][0] += int(ok)
        tallies["gate_correctness"][1] += 1
        print(f"   - gate_correctness: {'yes' if ok else 'no'}  — {rationale[:90]}")
        # LLM-judge scorers (routing / grounding / escalation).
        for s in scorers:
            try:
                fb = s(inputs=query, outputs=out)
                val = _feedback_value(fb)
                yes = val == "yes"
                tallies[s.name][0] += int(yes)
                tallies[s.name][1] += 1
                rationale = (getattr(fb, "rationale", "") or "")[:90]
                print(f"   - {s.name}: {val}  — {rationale}")
            except Exception as exc:
                print(f"   - {s.name}: ERROR {str(exc)[:80]}")

    print("\n" + "=" * 78 + "\n=== pass rates ===")
    for name, (yes, total) in tallies.items():
        pct = f"{100*yes/total:.0f}%" if total else "n/a"
        kind = "deterministic" if name == "gate_correctness" else "LLM judge"
        print(f"  {name}: {yes}/{total} = {pct}  ({kind})")
    return tallies


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    lim = next((int(a) for a in args if a.isdigit()), None)
    if "--harness" in args:
        evaluate(limit=lim)
    else:
        evaluate_direct(limit=lim)
