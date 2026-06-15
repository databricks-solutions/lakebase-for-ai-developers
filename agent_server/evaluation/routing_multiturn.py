"""Multi-turn routing A/B — verifies history-aware routing (`ROUTER_USE_HISTORY`).

The quality flywheel is single-turn (a fresh thread per question), so it cannot catch the failure
this targets: a referential follow-up ("and their pricing terms?") that routes correctly ONLY when
the supervisor sees the prior turn. This suite drives 2-turn threads through the REAL graph
(in-process, `MemorySaver` checkpointer) and scores TURN 2's route — once with history OFF and once
with it ON — so the lift is explicit.

Routing is isolated from data dependencies: `USE_STUBS=1` makes the gather nodes return fakes, so
no Lakebase / Vector Search / Genie is required. The router itself runs for real — the LLM route
(`llm_router_endpoint`) when workspace creds resolve, else the deterministic keyword fallback. The
LLM path is the real signal; the keyword fallback's both-surfaces default masks the lift on the
knowledge/analytics cases (it already contains them), so we surface which path ran.

Scoring is deterministic (no LLM judge): a turn passes when every expected agent is in the routed
set (extras allowed — the same tolerance as the flywheel's `routing_correctness` judge). We also
print the exact routed set per turn so a sharpening that doesn't change pass/fail
(`[knowledge, analytics]` → `[knowledge]`) is still visible.

Run:
  uv run agent-evaluate --routing-multiturn
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agent_server.config import settings
from agent_server.evaluation.runners import _pin_oauth_token, _setup_experiment
from agent_server.graph.build_graph import build_graph

# Each turn-2 is genuinely referential — alone it carries no strong topic/keyword, so it only
# routes to the expected agent once the prior turn is in scope. One case per gather agent.
ROUTING_MULTITURN = [
    {
        "label": "knowledge-followup",
        "turn1": "What do our Caterpillar contracts say about late-delivery penalties?",
        "turn2": "And their pricing terms?",
        "expected": ["knowledge"],
        "why": "'their' = the Caterpillar contracts → knowledge; the follow-up has no keyword of its own.",
    },
    {
        "label": "operational-followup",
        "turn1": "Find similar past quality incidents to Henkel's SKU-1001 adhesive cracking.",
        "turn2": "What about for our other adhesive suppliers?",
        "expected": ["operational"],
        "why": "Continues the similar-past-incidents search to other suppliers → operational.",
    },
    {
        "label": "analytics-followup",
        "turn1": "What is the total open PO quantity by supplier for Q4 2026?",
        "turn2": "And for Q1 next year?",
        "expected": ["analytics"],
        "why": "'And for Q1?' continues the aggregate roll-up → analytics.",
    },
]


async def _drive_no_resume(graph, cfg, question: str) -> None:
    """Run one turn to its natural stop (completion or HITL interrupt). We never resume — only the
    routing decision matters, and that's set by the supervisor before any gather/gate work."""
    turn_input = {"question": question, "user_id": cfg["configurable"]["user_id"],
                  "messages": [HumanMessage(content=question)]}
    async for _ in graph.astream(turn_input, cfg, stream_mode="updates"):
        pass


async def _route_turn2(graph, case: dict, use_history: bool) -> tuple[list, str]:
    """Drive turn1 then turn2 on a fresh same thread; return (turn2 routed agents, reasoning)."""
    settings.router_use_history = use_history
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex, "user_id": "eval@databricks.com"}}
    await _drive_no_resume(graph, cfg, case["turn1"])
    await _drive_no_resume(graph, cfg, case["turn2"])
    snap = await graph.aget_state(cfg)
    state = dict(snap.values) if snap else {}
    rd = state.get("route_decision")
    return (list(rd.agents) if rd else []), (rd.reasoning if rd else "")


async def _run_all() -> list[dict]:
    graph = build_graph(checkpointer=MemorySaver())
    rows: list[dict] = []
    for case in ROUTING_MULTITURN:
        off_agents, off_reason = await _route_turn2(graph, case, use_history=False)
        on_agents, on_reason = await _route_turn2(graph, case, use_history=True)
        exp = set(case["expected"])
        rows.append({
            "label": case["label"], "turn1": case["turn1"], "turn2": case["turn2"],
            "expected": case["expected"], "why": case["why"],
            "off_agents": off_agents, "off_pass": exp.issubset(set(off_agents)), "off_reason": off_reason,
            "on_agents": on_agents, "on_pass": exp.issubset(set(on_agents)), "on_reason": on_reason,
        })
    return rows


def _router_path(rows: list[dict]) -> str:
    fellback = any("keyword-fallback" in (r["off_reason"] + r["on_reason"]) for r in rows)
    return "keyword fallback (LLM router unavailable — LLM path is the real signal)" if fellback \
        else f"LLM router ({settings.llm_router_endpoint})"


def run_routing_multiturn() -> dict:
    """Run the A/B end-to-end: drive each case with history OFF then ON, score turn-2 routes, and
    print + persist a before/after table. Returns the structured rows + summary."""
    os.environ["USE_STUBS"] = "1"  # isolate routing from data deps (no Lakebase/VS/Genie needed)
    original_flag = settings.router_use_history
    _pin_oauth_token()
    _setup_experiment()
    try:
        rows = asyncio.run(_run_all())
    finally:
        settings.router_use_history = original_flag  # restore the process default

    off_acc = sum(r["off_pass"] for r in rows)
    on_acc = sum(r["on_pass"] for r in rows)
    n = len(rows)

    print("\n" + "=" * 92)
    print(f"=== MULTI-TURN ROUTING A/B  ·  router: {_router_path(rows)} ===")
    print("=" * 92)
    for r in rows:
        print(f"\n[{r['label']}]  expected={r['expected']}")
        print(f"  turn1: {r['turn1']}")
        print(f"  turn2: {r['turn2']}   ({r['why']})")
        print(f"    history OFF → {r['off_agents']}   {'PASS' if r['off_pass'] else 'FAIL'}")
        print(f"    history ON  → {r['on_agents']}   {'PASS' if r['on_pass'] else 'FAIL'}")
    print(f"\n=== turn-2 routing accuracy:  OFF {off_acc}/{n}   →   ON {on_acc}/{n}   "
          f"(lift +{on_acc - off_acc}) ===\n")

    summary = {"off_accuracy": off_acc, "on_accuracy": on_acc, "n": n,
               "lift": on_acc - off_acc, "router_path": _router_path(rows)}
    meta = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "router_endpoint": settings.llm_router_endpoint}
    out = Path(os.environ.get("FLYWHEEL_OUT_DIR", os.getcwd())) / "routing_multiturn_results.json"
    out.write_text(json.dumps({"meta": meta, "summary": summary, "rows": rows}, indent=2) + "\n")
    print(f"report → {out}")
    return {"summary": summary, "rows": rows, "meta": meta}


def main():
    run_routing_multiturn()


if __name__ == "__main__":
    main()
