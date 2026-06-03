# agent_server/ — WS1 (Spine, App & DABs) + WS5 (LangGraph, Routing)

> **Scaffold placeholder.** No agent code yet (Phase-0 decision). This README is the home for
> the graph, supervisor routing, Lakebase state wiring, MLflow autolog, and the App
> run/poll/resume handlers.

## How to start (Phase 0 → 1)

Copy the structure from the reference template rather than building from scratch:
https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced/agent_server

Key files to bring over and adapt:

| Template file | Purpose |
|---|---|
| `agent_server/agent.py` | Agent/graph logic, model, instructions, memory setup |
| `agent_server/start_server.py` | FastAPI server + MLflow autolog + Lakebase lifespan |
| `agent_server/utils_memory.py` | `AsyncCheckpointSaver` (short-term) + `AsyncDatabricksStore` (long-term) wiring |
| `agent_server/evaluate_agent.py` | MLflow scorers (P2) |
| `pyproject.toml` / `app.yaml` | deps + Apps config |

Then restructure into the multi-agent topology in [`../docs/architecture.md`](../docs/architecture.md):
`Supervisor → parallel Gather (Operational/Knowledge/Analytics) → Planner → Gate → [HITL] → Commit`.

## Skills to use
`quickstart`, `lakebase-setup`, `agent-memory`, `modify-agent`, `add-tools`, `deploy`,
`run-locally` (all in [`../.claude/skills/`](../.claude/skills/)). Plus installed
`databricks-lakebase`, `databricks-apps`, `databricks-dabs`.

## Reminders
- Short-term checkpointer is what HITL `interrupt()` resumes from — wire it first.
- Keep `interrupt()` out of any parallel step. Gather agents write distinct state keys; the
  planner fan-out writes the shared `plans` key (reducer). Bulk payloads → side tables.
- All `databricks` CLI calls need `--profile <p>`.
