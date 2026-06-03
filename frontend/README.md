# frontend/ — chat UI (WS1 + WS4)

> **Scaffold placeholder.** The reference template does **not** vendor the frontend — it clones
> it on demand when you run `uv run start-app`. Mirror that here rather than committing a copy.

## Reference frontend
Next.js chat app: https://github.com/databricks/app-templates/tree/main/e2e-chatbot-app-next

`start-app` clones it, runs `npm install && npm run build && npm run start` on port 3000, and
proxies to the agent server. See the template's `scripts/start_app.py`.

## What the demo UI must show (WS4)
- The **run/poll/resume** loop (App polls run state; long planning runs execute as background work).
- The **HITL approval card** at the `interrupt()` pause — approve / reject (edit + replan is P2).
- The run reading as a **narrative** for the demo (route → gather → plan → gate → approve → commit).

Banking accelerator shows a workflow-aware UI with SSE `workflow.state.updated` events and
stage badges — a good pattern to borrow:
https://github.com/databricks-industry-solutions/banking-agent-accelerator
