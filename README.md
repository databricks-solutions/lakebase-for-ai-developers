# mfg-supply-chain-copilot-agent

A multi-agent **Supply-Chain Planner Copilot** for manufacturing planners — built to showcase
**custom agents on Databricks with Lakebase**. LangGraph on **Databricks Apps**, all durable
state on **Lakebase** (Postgres), Genie + Mosaic AI Vector Search + Lakebase pgvector for
retrieval, human-in-the-loop approval, and MLflow tracing.

The demo proves Lakebase's value to the **technical GenAI/ML persona** (memory state, cost,
latency, scalability, I/O) and lands a defensible opinion on **Lakebase pgvector vs Mosaic AI
Vector Search**.

> **Status: P0 core loop landed end-to-end, P1 in progress.** The full graph runs against real
> Databricks backends *and* in-memory stubs: supervisor routing (LLM + keyword fallback), the
> three gather agents (Knowledge → Vector Search, Analytics → Genie, Operational → Lakebase
> pgvector hybrid similarity+join), the planner + deterministic gate, HITL `interrupt()` with
> durable **approve/reject resume** from the Lakebase checkpoint, and `commit`. State is on
> Lakebase via `AsyncCheckpointSaver` (short-term threads) + `AsyncDatabricksStore` (long-term
> semantic memory — approvals, preferences, supplier notes). MLflow autologs one resumable trace
> per run. The app ships a React/Vite chat UI with a Backend Explorer drawer, per-response
> 👍/👎 feedback logged to the trace, and an `agent-evaluation` flywheel (`agent_server/evaluation/`).
> **Next:** Knowledge-agent side-by-side comparison, parallel planner fan-out (`Send`), HITL
> edit + replan, DABs deploy hardening.

## Start here

1. **Read [`CLAUDE.md`](CLAUDE.md)** — the keystone: architecture, the pgvector/VS decision
   rule, state/memory model, conventions, scope tiers (P0/P1/P2), and open questions. Every
   Claude Code session should load it.
2. **Skills are already in the repo** — all 20 are vendored under [`.claude/skills/`](.claude/skills/)
   and load automatically in Claude Code (provenance + SHAs in
   [`.claude/skills/UPSTREAM.md`](.claude/skills/UPSTREAM.md)). Run
   [`scripts/install-skills.sh`](scripts/install-skills.sh) only to pull the *non-vendored* extras.
3. **Set up the env:** `cp .env.example .env` and fill in your `DATABRICKS_CONFIG_PROFILE` +
   workspace context (see [`CLAUDE.md` → Running locally vs. on Databricks](CLAUDE.md#running-locally-vs-on-databricks-auth--config)),
   then `uv sync` against [`pyproject.toml`](pyproject.toml) (the Phase-0 dependency contract).
4. **Verify the supervisor compiles & routes** —
   `USE_STUBS=1 uv run python -m agent_server.graph._smoke` runs the full path
   (supervisor → fan-out gather → planner → gate → HITL → commit) against in-memory fakes,
   no workspace needed.
5. **Pick your workstream** below and open its directory README.

## Run the app locally

The app is **two processes**: the FastAPI agent backend on `:8000` and the Vite/React frontend
on `:5173` (Vite proxies `/api/*` and `/invocations` → `:8000`). The router uses
`databricks-claude-haiku-4-5`; the planner uses `databricks-claude-opus-4-8`.

```bash
# one-time
uv sync                                    # Python deps
npm --prefix frontend install              # frontend deps
# auth once: databricks auth login --host https://<ws>.cloud.databricks.com --profile <name>
# ensure .env has DATABRICKS_CONFIG_PROFILE + catalog/schema/Lakebase/Genie set

# run — two terminals
uv run start-server                        # Terminal 1 — backend on :8000
npm --prefix frontend run dev              # Terminal 2 — frontend on :5173 (hot reload)
```

Then open **http://localhost:5173**.

- **Offline smoke test** (no workspace round-trips): `USE_STUBS=1 uv run python -m agent_server.graph._smoke`
  runs the full path against in-memory fakes. Stubs also kick in automatically when an endpoint
  isn't configured — good for exercising graph/HITL/memory wiring without burning Genie/VS calls.
- **Single-process alternative:** `npm --prefix frontend run build` then hit
  `http://localhost:8000/ui` (the backend serves the built SPA). Two terminals is better while
  iterating on agent code (hot reload).
- The UI's **Backend Explorer drawer** (`/api/explorer`) peeks at live Lakebase/pgvector/MLflow
  state during testing.

## Test questions

Seed-data anchors (use the exact values): **Henkel AG (SUP-001)**, hero SKU **SKU-1001**
(Structural Epoxy Adhesive), **40 on-hand** at DC-EAST, open POs **500** (Henkel, PO-2026-0042)
\+ **300** (DuPont, PO-2026-0043) = **760-unit gap**; Henkel status **at_risk (82.0)**; alternate
supplier **DuPont (SUP-002)**. Other clusters: **Nucor (SUP-005)** fasteners, **Saint-Gobain
(SUP-008)** abrasives. Demo "today" = 2026-06-05.

**Tier 1 — single-engine routing** (verify the router picks the right agent)

| Question | Routes to | Check |
|---|---|---|
| What is the total open PO quantity by supplier for Q4 2026? | analytics (Genie) | only `gather_analytics`; no approval |
| What do our Caterpillar contracts say about late-delivery penalties? | knowledge (VS) | only `gather_knowledge`; informational |
| Find similar past quality incidents to Henkel's SKU-1001 adhesive cracking. | operational (Lakebase) | hybrid SQL in trace; top-5 = Henkel cracking cluster, on_hand=40, open_po=500 joined |
| Which suppliers are currently flagged at risk? | analytics | Henkel + Saint-Gobain; latest-row rollup |

**Tier 2 — multi-agent + HITL approval** (the hero loop)

| Question | Routes to | Check |
|---|---|---|
| Henkel's SKU-1001 has recurring adhesive cracking — show me similar past cases joined to on-hand inventory and open POs, and recommend a mitigation. | operational (+analytics) | `interrupt()` fires → approval card; run pauses (Lakebase checkpoint); approve → `commit` writes memory |
| Nucor announced a carbon-steel price increase. Find related market-event notes and similar past incidents, and recommend whether to pre-buy. | knowledge + operational | 2 parallel gather spans; pre-buy = action-bearing → HITL |
| Recommend a mitigation for the SKU-1001 shortage given the Henkel risk, and give me total open POs by supplier for Q4. | operational + analytics | 3-way reasoning; gate trips on cost/action |

For the hero question, also test the **reject** path: approvals are always written (audit), but
preferences/supplier-notes are written **only on approve**.

**Tier 2b — write-back tables** (the structured plan persists to Postgres, visible on the **Lakebase** tab)

An action-bearing question proposes a structured plan; on the **Review** tab you approve / edit / hold
each action and commit, which writes rows to three Lakebase tables. Which table a row lands in is set
by the action kind:

| To fill… | Ask | Proposes (kinds) |
|---|---|---|
| **all three at once** | Henkel SKU-1001 keeps cracking — give me a full containment plan: hold the on-hand lot, quarantine the incoming PO, tighten incoming inspection, and hold Henkel until they're re-validated. | `quality_hold` + `quarantine_po` → approved_actions, `tighten_inspection` → planning_parameters, `supplier_quality_hold` → constraints |
| **`approved_actions`** | Henkel SKU-1001 is failing the adhesion test — quarantine the incoming 500-unit PO and bridge-source a buffer from DuPont. | `quarantine_po`, `split_source` |
| **`planning_parameters`** | Given the recurring SKU-1001 quality failures, raise the incoming-inspection level and bump safety stock until the defect is contained. | `tighten_inspection`, `raise_safety_stock` |
| **`constraints`** | Put a quality hold on Henkel/SUP-001 for SKU-1001 until they pass re-validation, and prioritize the EV inverter program for the constrained on-hand. | `supplier_quality_hold`, `allocation_constraint` |

Only **approved** actions write rows (held ones don't), and you can only act on kinds the planner
proposed — phrase the ask with the verbs above (*hold, quarantine, inspect, expedite, split/bridge from
DuPont, safety stock, hold the supplier, prioritize program X*) to elicit them.

**Tier 3 — memory & follow-ups** (the differentiator)

1. **Short-term referent resolution** — ask the hero question, approve, then in the *same* chat:
   *"What about DuPont for that same SKU instead?"* → planner resolves "that same SKU" = SKU-1001
   from checkpointed history (last 6 turns kept).
2. **Long-term cross-session recall** — seed memory via `GET /_seed_demo_memories` (or run/approve
   the hero question), then in a **new** chat ask *"How should we handle the Henkel SKU-1001
   coverage gap?"* → `hydrate_memory` recalls the prior approved decision and the planner cites it.
3. **Supplier-note scoping** — after an approved Henkel decision, ask an operational question that
   surfaces Henkel again → supplier note recalls only for suppliers the operational query returned.
4. **Access scope / governance** — switch identity to an out-of-scope user
   (`planner.bob@databricks.com`, scoped to fasteners/abrasives) and ask the operational SKU-1001
   question → adhesive rows are filtered out by the in-SQL `user_access` join.

**What to verify in each surface**

- **App UI:** step labels stream in order; approval card renders for Tier 2; thumbs up/down posts
  to `/api/feedback`; Explorer drawer shows live pgvector `quality_incidents` count.
- **MLflow traces:** one trace per run, resumable across the HITL pause; router `reasoning` field;
  the operational agent's **generated SQL** is captured; parallel gather spans overlap in time.
- **Lakebase state:** checkpoint row exists for the paused thread (survives a backend restart);
  after approve, the long-term store has rows in `approvals` / `preferences` / `supplier_notes`.

**Suggested 10-minute sequence:** Tier 1 #1 (clean single-engine) → Tier 1 #3 (show the hybrid
SQL) → Tier 2 hero (HITL approve + commit) → new chat, Tier 3 #2 (cross-session recall lights up).
That hits the router, all three gather engines, HITL, checkpoint durability, and long-term memory.

## Repo map

| Path | What |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Shared context — read first |
| [`.env.example`](.env.example) | Local config template (`cp` → `.env`); profile + workspace ctx |
| [`pyproject.toml`](pyproject.toml) | Dependency contract (`uv sync`) + the `start-server` entrypoint |
| [`databricks.yml`](databricks.yml) | DABs bundle (scaffold; empty resources) |
| [`.claude/skills/`](.claude/skills/) | Vendored template build skills + skills README |
| [`agent_server/`](agent_server/) | **WS1/WS4/WS5** — `start_server.py` (FastAPI + Lakebase lifespan), `agent.py` (@invoke/@stream), `webapp.py` (chat/sessions/feedback/explorer routes), `config.py`, `contracts.py`, `memory.py` (long-term store policy), `graph/` (supervisor + gather + planner + gate + HITL + commit), `tools/` (Knowledge / Genie / Operational + stubs), `evaluation/` (eval flywheel) |
| [`data/`](data/) | **WS2/WS3/WS4** — `knowledge/` PDF → Delta → VS index pipeline; `genie/` operational schema + programmatic space creation; (WS2) synthetic operational data + pgvector hybrid query land here |
| [`frontend/`](frontend/) | Claude-style chat UI — React + Vite SPA (dev on `:5173`, proxies to backend `:8000`; built SPA served at `/ui`) |
| [`scripts/`](scripts/) | setup/seed + skills installer |
| [`docs/architecture.md`](docs/architecture.md) | Multi-agent design, topology, pgvector/VS split, [end-to-end architecture diagrams](docs/architecture.md#end-to-end-architecture) |
| [`docs/storyboard.md`](docs/storyboard.md) | The 5 scenarios + persona |
| [`docs/sprint-plan.md`](docs/sprint-plan.md) | DoD, Must/Nice, workstreams, timeline, risks |
| [`docs/references.md`](docs/references.md) | All code/demo/doc links |

## Workstreams (begin here)

- **WS1 — Spine, App & DABs** (Alex & Kylie) → [`agent_server/`](agent_server/), [`databricks.yml`](databricks.yml)
- **WS2 — Operational / pgvector** (Chandhana) → [`data/`](data/)
- **WS3 — Genie (+ Vector Search)** (Ram / Kylie) → [`agent_server/`](agent_server/) (Genie tool)
- **WS4 — Planner + HITL + demo** → planner/gate/`interrupt()`, seed data, run-of-show
- **WS5 — LangGraph, Routing** (Ram) → [`agent_server/`](agent_server/)

## Primary reference

Lean heavily on the
[`agent-langgraph-advanced`](https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced)
template (LangGraph + Lakebase memory + MLflow; the template's own UI is Next.js — **this repo's
frontend is Vite + React**; ships its own skills). Full link list in
[`docs/references.md`](docs/references.md).
