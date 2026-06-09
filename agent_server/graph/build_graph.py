"""Assemble the supervisor StateGraph.

Topology (mirrors `docs/architecture.md`):

    START → supervisor → (fan-out via conditional edge) → [gather_*] → planner
          → gate → (hitl_review if needs_approval, else commit) → END

Gather nodes write distinct state keys (no reducer). The fan-out uses
`add_conditional_edges` returning a *list* of target node names — LangGraph runs them
together in the next superstep, and the planner naturally fans-in once they all complete.

The checkpointer hookup is left as an optional argument so this module stays runnable
without a Lakebase connection. WS1 wires `AsyncCheckpointSaver` in when stateful
sessions land.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent_server.graph.gather_nodes import (
    analytics_node,
    knowledge_node,
    memory_node,
    operational_node,
    route_to_gatherers,
)
from agent_server.graph.planner import (
    commit_node,
    gate_router,
    hitl_review_node,
    planner_node,
)
from agent_server.graph.state import AgentState
from agent_server.graph.supervisor import supervisor_node


GATHER_NODE_NAMES = ("gather_knowledge", "gather_analytics", "gather_operational", "gather_memory")


def build_graph(checkpointer=None):
    """Construct and compile the supervisor graph.

    Args:
        checkpointer: optional LangGraph checkpointer (e.g. `AsyncCheckpointSaver`).
                      Required for HITL `interrupt()` and resumable runs; safely omitted
                      for stateless test invocations.
    """
    builder = StateGraph(AgentState)

    # Nodes
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("gather_knowledge", knowledge_node)
    builder.add_node("gather_analytics", analytics_node)
    builder.add_node("gather_operational", operational_node)
    builder.add_node("gather_memory", memory_node)
    builder.add_node("planner", planner_node)
    builder.add_node("hitl_review", hitl_review_node)
    builder.add_node("commit", commit_node)

    # Entry → supervisor
    builder.add_edge(START, "supervisor")

    # Supervisor → fan-out (LangGraph runs all returned targets in the next superstep)
    builder.add_conditional_edges("supervisor", route_to_gatherers, list(GATHER_NODE_NAMES))

    # All gather nodes converge on the planner (natural fan-in)
    for n in GATHER_NODE_NAMES:
        builder.add_edge(n, "planner")

    # Planner → gate → HITL or commit
    builder.add_conditional_edges("planner", gate_router, ["hitl_review", "commit"])
    builder.add_edge("hitl_review", "commit")
    builder.add_edge("commit", END)

    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_graph"]
