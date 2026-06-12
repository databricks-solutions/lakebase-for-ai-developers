# agent_server/ — WS1 (Spine, App & DABs) + WS5 (LangGraph, Routing)

The implemented Supply-Chain Planner Copilot: a LangGraph `StateGraph` (not a message-based
ReAct agent) that routes a planner's question to the right data surfaces, composes a grounded
recommendation, gates it through human-in-the-loop approval, and commits the decision to durable
state on Lakebase — all as **one MLflow trace**. It runs in-process on a single Databricks App:
`databricks_ai_bridge.LongRunningAgentServer` provides the MLflow Responses API + run/poll/resume
transport and the Lakebase lifespan; `webapp.py` mounts a React SPA + JSON API on the same app.

See [`../docs/architecture.md`](../docs/architecture.md) for the end-to-end diagrams and
[`../CLAUDE.md`](../CLAUDE.md) for the project context, stack, and routing guidance.

## Graph topology

Assembled in [`graph/build_graph.py`](graph/build_graph.py), keyed on `AgentState`:

```
START → supervisor ──(conditional fan-out)──▶ gather_operational ┐
                                              gather_knowledge    ├─▶ hydrate_memory → planner
                                              gather_analytics    ┘                        │
                                                                                           ▼
                                                                         gate_router (deterministic)
                                                                            │              │
                                                          needs_approval ───┘              └─── else
                                                                            ▼                    │
                                                                       hitl_review               │
                                                                       interrupt()               │
                                                                            │                    ▼
                                                                            └──────────────▶ commit → END
```

- **`supervisor`** routes via `ChatDatabricks` (router endpoint) with structured output → a
  `RouterDecision` (subset of `knowledge`/`analytics`/`operational`); falls back to a deterministic
  keyword router if the LLM/endpoint is unavailable, so the graph stays runnable offline.
- **Parallel gather.** The supervisor's conditional edge returns a *list* of gather node names, so
  LangGraph runs the chosen ones together in one superstep. Each writes a **distinct state key**
  (no reducer), so they can't collide. They fan in naturally on `hydrate_memory`.
- **`hydrate_memory`** runs after the fan-in (so supplier-note recall can scope to suppliers the
  operational agent surfaced), recalls long-term memory, and writes `memory_context`.
- **`planner`** (`ChatDatabricks`, planner endpoint) composes a `PlannerRecommendation` +
  structured `planned_actions`; **sequential** today (per-SKU fan-out is the P2 stretch).
- **`gate_router`** is deterministic (not the LLM): route to `hitl_review` when the recommendation
  is action-bearing **or** `est_cost_usd ≥ $50k` (`APPROVAL_COST_THRESHOLD_USD`), else `commit`.
- **`hitl_review`** is a real `interrupt()` — kept **downstream of the fan-in, out of any parallel
  step**. The run pauses durably on the checkpoint and resumes via `Command(resume=HITLDecision)`.
- **`commit`** writes curated long-term memory + the Meridian relational write-back, then ends.

## Gather agents & auth

Each gather node has a real impl and a stub (`USE_STUBS=1`), degrades to an empty result rather
than failing the run, and emits its generated SQL where applicable for traceability/scoring.

| Agent (node) | Backend | Auth |
|---|---|---|
| Operational (`gather_operational`) | Lakebase hybrid SQL — pgvector `quality_incidents` JOIN `inventory_current`/`open_pos` + in-query `user_access` access-scope predicate, one statement ([`tools/operational_tool.py`](tools/operational_tool.py)) | **App service principal** (Lakebase OAuth) |
| Knowledge (`gather_knowledge`) | Mosaic AI Vector Search, hybrid BM25 + ANN over the document corpus ([`tools/knowledge_tool.py`](tools/knowledge_tool.py)) | **OBO** (forwarded user token); falls back to local U2M / app SP |
| Analytics (`gather_analytics`) | Genie Conversation API, NL→SQL over governed tables ([`tools/genie_tool.py`](tools/genie_tool.py)) | **OBO**; falls back to app SP / ambient |

OBO plumbing lives in [`obo.py`](obo.py): the `X-Forwarded-Access-Token` header is captured
per-request by an ASGI middleware in [`webapp.py`](webapp.py) into a contextvar (copied into
LangGraph's node executor), so the knowledge/Genie tools call **as the user** and UC governs each
caller's access. Absent a token (local dev, eval, background task), tools fall back to the app SP.

## State & memory on Lakebase

All durable state lives on Lakebase (autoscaling Postgres). DB credentials are short-lived OAuth,
rotated by the SDK — **no Postgres password in config or `.env`**. Three schemas:

| Schema (default) | Owner | Holds |
|---|---|---|
| `public` (`LAKEBASE_OPERATIONAL_SCHEMA`) | platform `databricks_writer_*` | Operational rows — pgvector `quality_incidents` + synced relational tables. **SELECT-only for the SP.** |
| `supply_chain_planner_memory` (`LAKEBASE_AGENT_MEMORY_SCHEMA`) | App SP | `AsyncCheckpointSaver` (short-term checkpoints; HITL resumes here) + `AsyncDatabricksStore` (long-term + semantic; the pgvector memory path). Wired in [`lakebase.py`](lakebase.py). |
| `supply_chain_planner_app` (`LAKEBASE_WRITEBACK_SCHEMA`) | App SP | Meridian write-back: `approved_actions` / `planning_parameters` / `constraints`. The SP CREATEs + owns them at startup ([`operational_db.py`](operational_db.py)). |

A fourth, hard-coded `agent_server` schema backs the durable run/poll/resume store
(`databricks_ai_bridge.long_running`); `ensure_durable_schema()` makes the SP own it before init.

**Long-term memory namespaces** (policy + curated `memory_text` embedding in [`memory.py`](memory.py)):

- `("approvals", <user>)` — audit trail of **every** committed decision (both verdicts).
- `("preferences", <user>)` — distilled planner preferences (approved **and** action-bearing only).
- `("supplier_notes", <supplier_id>)` — cross-user learned supplier facts (approved + action-bearing), scoped per supplier surfaced operationally.

## HTTP surface

`LongRunningAgentServer` serves the MLflow Responses API; `webapp.py` adds the human-facing routes
on the **same** FastAPI app (chat proxy disabled; `/` redirects to `/ui/`).

| Route | Purpose |
|---|---|
| `/invocations`, `/poll`, `/resume` | MLflow Responses API — run/poll/resume; HITL resumes with an `hitl_verdict` in `custom_inputs` ([`agent.py`](agent.py)) |
| `/ui` | React SPA (built from `frontend/dist`) |
| `/api/me` | OBO caller identity (+ in-scope check vs the seeded planner) |
| `/api/sessions` | Per-user conversation history + transcripts (kept in the LangGraph store) |
| `/api/explorer`, `/api/explorer/*` | Deep links + live peeks into each backend (Lakebase, pgvector, MLflow, VS, Genie, UC) |
| `/api/chat/stream` | SSE chat: drives the graph, streams step progress + the final answer; starts or resumes a HITL run |
| `/api/state/tables` | Reads a thread's committed write-back rows + recalled approval memory |
| `/api/feedback` | Logs 👍/👎 as an MLflow assessment on the run's trace |

## Models

Two LLM callsites ([`config.py`](config.py)); gather agents use VS/Genie/Lakebase, not an LLM.

| Callsite | Default endpoint | Env override |
|---|---|---|
| Router (`supervisor`) — fast classifier | `databricks-claude-haiku-4-5` | `LLM_ROUTER_ENDPOINT` |
| Planner — complex synthesis | `databricks-claude-opus-4-8` | `LLM_PLANNER_ENDPOINT` |

> No `temperature` is passed — Opus-class models reject the param (BAD_REQUEST).

## File map

| File | What it does |
|---|---|
| [`graph/build_graph.py`](graph/build_graph.py) | Assembles + compiles the `StateGraph`; nodes, edges, fan-out, fan-in |
| [`graph/supervisor.py`](graph/supervisor.py) | Router node — LLM structured route, deterministic keyword fallback |
| [`graph/gather_nodes.py`](graph/gather_nodes.py) | The three gather nodes + `route_to_gatherers` fan-out edge; real impl / stub switch |
| [`graph/memory_nodes.py`](graph/memory_nodes.py) | `hydrate_memory_node` — scoped semantic recall into `memory_context` |
| [`graph/planner.py`](graph/planner.py) | Planner LLM + deterministic gate + `interrupt()` HITL + commit (memory + write-back) |
| [`graph/state.py`](graph/state.py) | `AgentState` (the typed graph state schema) |
| [`agent.py`](agent.py) | `@invoke`/`@stream` handlers; graph→Responses translation; MLflow autolog + UC trace binding |
| [`start_server.py`](start_server.py) | `LongRunningAgentServer` + Lakebase lifespan (schema/table provisioning, resource reuse) |
| [`webapp.py`](webapp.py) | OBO middleware, SPA mount, `/api/*` routes, SSE chat, feedback |
| [`lakebase.py`](lakebase.py) | Checkpointer + store wiring, connection config priority, OAuth-rotated creds |
| [`operational_db.py`](operational_db.py) | Operational pool + query embedding; write-back DDL/upserts; schema-ownership guards |
| [`memory.py`](memory.py) | Long-term memory namespaces, curated write policy, scoped recall |
| [`obo.py`](obo.py) | OBO token capture vs app-SP fallback |
| [`config.py`](config.py) | One env-driven `settings` source of truth (UC, Lakebase, VS, Genie, MLflow, LLM endpoints) |
| [`contracts.py`](contracts.py) | Phase-0 Pydantic I/O contracts shared by every node, stub, and real impl |
| [`tools/`](tools/) | `operational_tool.py` (Lakebase hybrid SQL), `knowledge_tool.py` (VS), `genie_tool.py` (Genie), `stubs.py` |

## Conventions

- **Keep `interrupt()` out of any parallel step** — it lives in `hitl_review`, downstream of the
  gather fan-in.
- **Gather nodes write distinct state keys** (no reducer); a planner fan-out (P2) would write a
  shared key with a reducer. Bulk payloads stay in side tables / write-back; state holds references.
- **Build against the contracts** in `contracts.py` — change them only by team decision. Stubs and
  real impls must produce identical shapes.
- The Operational agent always returns its generated SQL so the join + access scope are traceable
  and scorable. Every `databricks` CLI call needs `--profile <p>`.
