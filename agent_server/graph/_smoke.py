"""Offline smoke test for the supervisor graph.

Runs the compiled graph end-to-end against the in-memory stubs (no workspace required).
Useful for verifying wiring after changing nodes / contracts. Invoke with:

    USE_STUBS=1 uv run python -m agent_server.graph._smoke
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("USE_STUBS", "1")

from agent_server.graph.build_graph import build_graph


def main() -> None:
    graph = build_graph()
    question = (
        "Nucor just announced a price increase on carbon steel. "
        "Show me total open POs by supplier for Q4 and any similar past quality issues."
    )

    result = graph.invoke({"question": question, "user_id": "smoke@example.com"})

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
    main()
