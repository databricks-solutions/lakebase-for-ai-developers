"""Offline smoke test for the supervisor graph.

Runs the compiled graph end-to-end against the in-memory stubs (no workspace required), now
exercising the real HITL `interrupt()`: the graph pauses for approval, and we resume with an
APPROVED verdict. Uses an in-memory checkpointer (interrupt() requires one). Invoke with:

    USE_STUBS=1 uv run python -m agent_server.graph._smoke
"""

from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("USE_STUBS", "1")

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent_server.contracts import HITLDecision, HITLVerdict
from agent_server.graph.build_graph import build_graph


async def _main() -> None:
    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "smoke-1", "user_id": "smoke@example.com"}}
    question = (
        "Nucor just announced a price increase on carbon steel. "
        "Show me total open POs by supplier for Q4 and any similar past quality issues."
    )

    result = await graph.ainvoke({"question": question, "user_id": "smoke@example.com"}, config)

    # If the gate tripped, the run paused at the HITL interrupt — resume with approval.
    if "__interrupt__" in result:
        intr = result["__interrupt__"][0]
        print("\n── HITL interrupt (approval card) ──")
        print(json.dumps(intr.value, indent=2, default=str)[:600])
        decision = HITLDecision(
            verdict=HITLVerdict.APPROVED, note="approved by smoke", user_id="smoke@example.com"
        )
        result = await graph.ainvoke(Command(resume=decision.model_dump()), config)

    print("\n── route decision ──")
    rd = result.get("route_decision")
    print(rd.model_dump() if rd else None)

    print("\n── trace ──")
    for note in result.get("trace_notes", []):
        print(f"  • {note}")

    print("\n── recommendation ──")
    rec = result.get("recommendation")
    if rec:
        print(json.dumps(rec.model_dump(), indent=2))

    print("\n── hitl ──")
    hitl = result.get("hitl_decision")
    print(hitl.model_dump() if hitl else None)


if __name__ == "__main__":
    asyncio.run(_main())
