# Claude Code Skills

This repo ships skills so every engineer's Claude Code sessions share the same tested
commands, patterns, and troubleshooting steps. There are **three** skill sources — all
**vendored (committed) and pinned**. Provenance + commit SHAs + refresh steps are in
[`UPSTREAM.md`](UPSTREAM.md).

## 1. Vendored template skills (in this directory)

Copied verbatim from
[`databricks/app-templates` → `agent-langgraph-advanced/.claude/skills`](https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced/.claude/skills).
These are the primary build skills for a LangGraph-on-Apps agent with Lakebase memory:

| Skill | What it covers |
|-------|----------------|
| `quickstart` | First-time setup: auth, profile, Lakebase config, MLflow experiment (`uv run quickstart`) |
| `lakebase-setup` | Provisioned vs autoscaling Lakebase, connectivity, permissions |
| `agent-memory` | Short-term checkpointer + long-term store patterns; `thread_id` / `user_id` usage |
| `discover-tools` | List workspace resources (UC functions, Vector Search, Genie, endpoints) |
| `create-tools` | Build tool resources: UC functions/connections, Genie space, Vector Search index, local Python tools |
| `add-tools` | Wire a tool into the agent **and** grant permissions in `databricks.yml` (many `examples/*.yaml`) |
| `deploy` | Deploy to Databricks Apps via DABs; bind/replace an existing app |
| `run-locally` | Local run loop (`uv run start-app`/`start-server`; this repo's frontend is Vite — `npm --prefix frontend run dev` for live UI) |
| `modify-agent` | Edit agent code: system prompt, model, tools |
| `load-testing` | Load-test the deployed agent |
| `migrate-from-model-serving` | Move an existing Model Serving agent onto this template |

> **Note:** These skills assume the template's `agent_server/` + `scripts/` layout, which is now
> in this repo (see [`agent_server/README.md`](../../agent_server/README.md)), so they work as
> procedural guides for the live code — with two repo-specific deltas: the frontend is **Vite +
> React** (not the template's Next.js), and the app entrypoint is `start-server` (with `start-app`
> kept as an alias). The template's `AGENTS.md` also references `supervisor-api` /
> `supervisor-api-background-mode` skills — pull those from the template if/when the team adopts
> the Supervisor API path.

## 2. Databricks Agent Skills (vendored, pinned — selective)

From [`databricks/databricks-agent-skills`](https://github.com/databricks/databricks-agent-skills)
@ `00d0daf` — the ~5 most relevant skills plus their parent:

| Skill | What it covers |
|-------|----------------|
| `databricks-core` | CLI auth, profiles, data exploration, bundles (parent of the others) |
| `databricks-lakebase` | Lakebase Postgres: projects, scaling, connectivity, synced tables, pgvector |
| `databricks-vector-search` | Mosaic AI Vector Search endpoints/indexes, search modes, RAG |
| `databricks-dabs` | Declarative Automation Bundles (DABs) — validate/deploy/run |
| `databricks-apps` | Build on the Databricks Apps platform |
| `databricks-model-serving` | Model Serving endpoints (LLM/custom/external) |

CLI compatibility: skill commands require **Databricks CLI ≥ v0.294.0**. The remaining stable +
experimental skills (`databricks-jobs`, `databricks-pipelines`, `databricks-mlflow-evaluation`,
`databricks-agent-bricks`, …) are **not vendored** — pull them with `scripts/install-skills.sh`
(`databricks aitools install`, requires CLI ≥ v1.0.0).

## 3. MLflow Skills (vendored, pinned — selective)

From [`mlflow/skills`](https://github.com/mlflow/skills) @ `b90eca1`:
`instrumenting-with-mlflow-tracing`, `agent-evaluation`, `analyze-mlflow-trace`. The rest
(chat-session/trace retrieval, metrics, onboarding, docs search) are available via
`npx skills add mlflow/skills`.

---

See [`UPSTREAM.md`](UPSTREAM.md) for exact SHAs + refresh steps, `scripts/install-skills.sh`
for pulling the non-vendored skills, and [`docs/references.md`](../../docs/references.md) for all links.
