# mfg-supply-chain-copilot-agent

A multi-agent **Supply-Chain Planner Copilot** for manufacturing planners — built to showcase
**custom agents on Databricks with Lakebase**. LangGraph on **Databricks Apps**, all durable
state on **Lakebase** (Postgres), Genie + Mosaic AI Vector Search + Lakebase pgvector for
retrieval, human-in-the-loop approval, and MLflow tracing.

The demo proves Lakebase's value to the **technical GenAI/ML persona** (memory state, cost,
latency, scalability, I/O) and lands a defensible opinion on **Lakebase pgvector vs Mosaic AI
Vector Search**.

> **Status: scaffold.** This repo currently holds shared context, skills, and references so the
> workstreams can begin building. Agent code, I/O contracts, and a runnable app are the first
> sprint tasks (see the sprint plan).

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
4. **Pick your workstream** below and open its directory README.

## Repo map

| Path | What |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Shared context — read first |
| [`.env.example`](.env.example) | Local config template (`cp` → `.env`); profile + workspace ctx |
| [`pyproject.toml`](pyproject.toml) | Phase-0 dependency contract (`uv sync`); no entrypoints yet |
| [`databricks.yml`](databricks.yml) | DABs bundle (scaffold; empty resources) |
| [`.claude/skills/`](.claude/skills/) | Vendored template build skills + skills README |
| [`agent_server/`](agent_server/) | **WS1/WS5** — graph spine, routing, Lakebase state, App handlers |
| [`data/`](data/) | **WS2/WS4** — synthetic data, embeddings, pgvector hybrid query, Acme seed |
| [`frontend/`](frontend/) | chat UI (references `e2e-chatbot-app-next`, cloned on demand) |
| [`scripts/`](scripts/) | setup/seed + skills installer |
| [`docs/architecture.md`](docs/architecture.md) | Multi-agent design, topology, pgvector/VS split |
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
template (LangGraph + Lakebase memory + MLflow + Next.js, ships its own skills). Full link list
in [`docs/references.md`](docs/references.md).
