# AGENTS.md — Supply-Chain Planner Copilot

> Shared context for every engineer's Codex sessions. Read this first so sessions stay
> aligned on the use case, stack, environment, and architecture. The shared state schema and
> I/O contracts live in [`agent_server/contracts.py`](agent_server/contracts.py) — build against
> them; changing one is a team decision.

## Codex workflow

- Inspect `git status`, recent commits, and relevant files before proposing a change. Preserve
  unrelated user changes in a dirty worktree.
- Read every relevant repo skill completely before acting. If one skill requires another, load both.
- Prefer small, contract-respecting changes and verify the narrowest relevant path first.
- Treat reviews and diagnoses as read-only unless the user also asks for implementation.
- Do not deploy, mutate live workspace resources, commit, or push unless the user authorizes it.

## Project & goal

A multi-agent **Supply-Chain Planner Copilot** for manufacturing planners. A planner asks a
question; the system routes it to the right engine, produces a recommendation with
**human-in-the-loop approval**, remembers prior sessions, and is traced end-to-end with MLflow.

It is a demo that shows how to build a **stateful agent on Databricks with Lakebase** — agent
memory, session/tool state, and operational semantic retrieval in one Postgres backend — and
when to use **Lakebase + LangGraph** vs **Mosaic AI Vector Search**.

**Canonical demo scenario:** *"Show me similar quality issues for this supplier, joined to on-hand
inventory and open POs"* → recommendation → gate → HITL approval → commit, visible as one MLflow
trace and resumable from the Lakebase checkpoint across the pause.

> **Access control:** operational reads run as the app **service principal**, so every
> authenticated app user sees the same UC-governed data. Per-user product-code scoping is
> intentionally **out of scope** — it was a demo-only `user_access` ACL that silently returned
> nothing for any unseeded user (every FE/customer). In production it would be added via Postgres
> RLS keyed on `current_user()` (with per-user/OBO DB connections) or an entitlements join driven
> by a real identity source.

## Architecture

LangGraph on **Databricks Apps** (not Model Serving — Apps runs the graph in-process as
background work with the UI polling state; the durable checkpoint makes a run resumable across
app restarts). **All durable state lives on Lakebase (Postgres).** MLflow autologs traces.

```
Supervisor (router) → parallel Gather → Planner (fan-out per SKU/supplier) → Aggregate + Gate → [HITL interrupt()] → Commit
```

End-to-end diagrams: [`docs/architecture.md` → End-to-end architecture](docs/architecture.md#end-to-end-architecture).

The **gather phase** has three sibling retrieval/data agents:

- **Operational — Lakebase.** Semantic similarity as *one predicate* in a governed relational
  query that joins live operational rows (inventory, open POs) in a single SQL statement. Output:
  rows with operational context.
- **Knowledge — Mosaic AI Vector Search.** Large, slow-changing unstructured corpus (contracts,
  SOPs, risk/incident reports). Output: passages for grounding.
- **Analytics — Genie.** NL→SQL aggregation over governed tables, via the Genie Conversation
  API wrapped as a tool.

**Routing guidance:**
- **Lakebase** when the agent needs memory + semantic similarity + live SQL joins in one
  operational query path (similarity is one predicate inside a relational/operational query).
- **Mosaic AI Vector Search** for large-scale managed RAG over broad document corpora (managed
  ingestion, hybrid retrieval, reranking, large vector counts).
- **Genie** for structured business questions (e.g. *"total unfulfilled demand by product code
  for Q4/Q1?"*).

## State & memory (all on Lakebase, managed by LangGraph)

Lakebase is the OLTP backend; the LangGraph integration in `databricks-langchain` manages the
tables, connection pooling, and vector indexing for you — **you do not hand-manage a pgvector
client**:

- **Short-term — `AsyncCheckpointSaver`.** Thread/session state and checkpoints. Runs are
  resumable; HITL `interrupt()` resumes from here.
- **Long-term + semantic — `AsyncDatabricksStore`.** A Lakebase-backed LangGraph Postgres store
  with embeddings configured via a Databricks embedding endpoint. **This is the pgvector path** —
  it persists memory + vectors in Postgres tables (`store`, `store_vectors`, migrations) and
  serves semantic search over stored memory. Holds preferences, prior approvals, learned
  supplier notes; hydrated at run start, written at commit.
- **Operational hybrid query.** For the canonical scenario, query the same Lakebase backend
  directly so vector similarity + SQL joins resolve in one governed statement — rather than pulling
  IDs from a vector index and round-tripping to Postgres to join and re-filter.

```python
from databricks.sdk import WorkspaceClient
from databricks_langchain import AsyncCheckpointSaver, AsyncDatabricksStore, ChatDatabricks
from langchain.agents import create_agent

w = WorkspaceClient()
checkpointer = AsyncCheckpointSaver(project=PROJECT, branch=BRANCH, workspace_client=w, schema=SCHEMA)
store = AsyncDatabricksStore(project=PROJECT, branch=BRANCH, workspace_client=w, schema=SCHEMA,
                            embedding_endpoint=EMBEDDING_ENDPOINT, embedding_dims=1024)
await store.setup()
agent = create_agent(model=ChatDatabricks(endpoint=LLM_ENDPOINT), tools=[...],
                     checkpointer=checkpointer, store=store, state_schema=AgentState)
```

**LangGraph state discipline:** gather agents write **distinct** state keys (no reducer); the
planner fan-out writes the shared `plans` key (reducer required). Bulk payloads go to side
tables; state holds references (every superstep serializes the full snapshot). Keep
`interrupt()` in a review node **after** fan-in, out of any parallel step.

## Tech stack & key dependencies

Mirror the reference template's `pyproject.toml`:

- **App/runtime:** Databricks Apps, `fastapi`, `uvicorn`, `uv`.
- **Agent:** `langgraph>=1.1.0`, `databricks-langchain[memory]>=0.19.0` (`AsyncCheckpointSaver` +
  `AsyncDatabricksStore`), `databricks-ai-bridge[agent-server]`, `langchain-mcp-adapters`.
- **Models:** `ChatDatabricks` (Foundation Model APIs); a Databricks embedding endpoint
  (`databricks-gte-large-en`) configured in the store.
- **Platform:** `databricks-sdk`, `databricks-agents`, `mlflow>=3.10.1` (autolog).
- **Lakebase:** managed Postgres (autoscaling) as the OLTP backend; the checkpointer/store own
  their tables, pooling, and vector index; runtime OAuth DB credentials via the SDK. **Synced
  Tables** sync operational Delta tables → Lakebase for the operational joins.
- **Genie:** Conversation API.
- **Frontend:** Vite + React + TypeScript SPA (committed in `frontend/`, built to `frontend/dist`,
  served by the agent at `/ui`). Live dev: `npm --prefix frontend run dev` (Vite on :5173,
  proxies `/api` + `/invocations` → :8000); `uv run start-server` (or its `start-app` alias)
  serves the built SPA.

## Running locally vs. on Databricks (auth & config)

**The same code runs both in the IDE and on Databricks** — for data gen, the store, Vector
Search, Genie, and the agent. Only **how it authenticates** changes; the Databricks SDK
credential chain handles both:

- **Local (IDE / scripts / notebooks on your laptop):** auth from a **`.env`** with
  **`DATABRICKS_CONFIG_PROFILE`** (OAuth U2M via a CLI profile, preferred over a PAT). Load it
  with `python-dotenv`; then `WorkspaceClient()` / `ChatDatabricks` / the store pick up the profile.
- **On Databricks (notebook, job, or App):** auth is **ambient** — the runtime/app service
  principal provides credentials. Do **not** set a profile/host/token there.

**Make code environment-aware** — only load `.env` when not on Databricks (DBR/Apps set
`DATABRICKS_RUNTIME_VERSION`):

```python
import os
if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):  # local only
    from dotenv import load_dotenv
    load_dotenv()
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()  # local: uses DATABRICKS_CONFIG_PROFILE; on Databricks: ambient creds
```

- **Lakebase credentials, both environments:** no Postgres password in `.env` — the
  checkpointer/store generate short-lived OAuth DB credentials via the SDK and rotate them.
- **`.env` is git-ignored.** Start from [`.env.example`](.env.example): `cp .env.example .env`.

**Spark, both environments — use `get_spark()`, never a bare `spark` global.** Code that needs
Spark (data-gen / setup only) must run identically in a Databricks notebook/job and locally. Get
the session from [`data/_spark.py`](data/_spark.py): it returns the **ambient** session on
Databricks and a **Databricks Connect** session locally (auth via `DATABRICKS_CONFIG_PROFILE`).

```python
from data._spark import get_spark
spark = get_spark()  # ambient on Databricks; Databricks Connect locally
```

- **Spark is a data-layer concern — it lives in `data/`, never in `agent_server/`.** The agent app
  doesn't use Spark, and `databricks-connect` is a **dev-only** dependency that must *never* be
  installed on Databricks (it conflicts with the runtime pyspark). For serverless local compute,
  set `serverless_compute_id = auto` in your CLI profile.
- **One config source of truth:** `agent_server/config.py` → `settings` (typed, env-driven). Every
  module (app *and* data scripts) reads catalog/schema/endpoints from it — don't hardcode or
  re-read env elsewhere. (It stays in `agent_server` so it ships with the app wheel; it imports no
  agent/Spark code, so data scripts importing it stays cheap.)

**Common commands**

```bash
cp .env.example .env                                                              # seed local config
databricks auth login --host https://<ws>.cloud.databricks.com --profile <name>  # OAuth U2M
databricks auth profiles                                                          # list/verify
uv sync                                                                           # local env from pyproject.toml
uv run python -c "from databricks.sdk import WorkspaceClient; print(WorkspaceClient().current_user.me().user_name)"
```

Every `databricks` CLI command needs the profile (`--profile <p>` or `DATABRICKS_CONFIG_PROFILE=<p>`).

**Deploying** — one command; all logic lives in [`scripts/deploy.sh`](scripts/deploy.sh)
(idempotent, cold-start-safe, graceful per-step degradation). Full walkthrough: [`docs/DEPLOY.md`](docs/DEPLOY.md).

```bash
make deploy      PROFILE=<p>              # full one-shot: preflight · Lakebase project · build · deploy · seed · Genie · verify
make deploy      PROFILE=<p> TARGET=demo  # clean prod-style names (default: dev)
make redeploy    PROFILE=<p>              # FAST: agent-server code change → bundle deploy + bundle run (~30-60s)
make redeploy-ui PROFILE=<p>              # FAST: frontend change → npm build + deploy + run
```

- **`bundle deploy` ≠ `bundle run`.** `bundle deploy` only uploads source + reconciles resources;
  `bundle run <app-key>` creates the active deployment that makes the new code live. The fast loops do both.
- **Never delete the app — redeploy in place.** The app SP (and its Lakebase-owned schemas) is stable
  across redeploys but **destroyed on app delete**, which orphans the schemas (`databricks_superuser`
  can't reassign them — no `SET ROLE`). If you must recreate, **detach the Lakebase resource as
  `CAN MANAGE` first** (the platform reassigns the SP's objects and drops the role cleanly). See
  [`docs/lakebase-apps-permissions.md`](docs/lakebase-apps-permissions.md).

## Repo layout

| Path | Area | Holds |
|------|------|-------|
| `agent_server/` | Spine, App, DABs & routing | Graph skeleton, supervisor routing, checkpointer+store wiring, MLflow autolog, App run/poll/resume handlers |
| `data/` | Operational & demo data | Synthetic data gen, operational tables, the hybrid similarity+join query; seeded demo scenario |
| `agent_server/` (Genie tool) | Genie & Vector Search | Genie space wrapper; VS Knowledge agent |
| `agent_server/` (planner/HITL) | Planner & HITL | Planner node, gate/threshold, `interrupt()` approval card, run-of-show |
| `databricks.yml` | Deploy / DABs | DABs bundle: App resource, Lakebase, Genie space, experiment, setup/seed job, `dev`/`demo` targets |
| `docs/` | Docs | Architecture, storyboard, references |
| `.env.example` | Config | Local config template (`cp` → `.env`) — see auth section above |
| `.agents/skills/` | Codex skills | Full Codex-native ports of the pinned build + Databricks + MLflow skills |
| `.claude/skills/` | Claude skills | Independently maintained Claude Code variants of the same workflows |

## Capability tiers (core loop first, then enhancements)

- **Core loop:** supervisor + routing · Operational agent (hybrid similarity + join to
  on-hand/POs) · Genie Analytics agent · planner + gate · short-term checkpointer ·
  HITL approve/reject via `interrupt()` · MLflow autolog · Databricks App (run/poll/resume) ·
  seeded demo dataset · OBO auth · DABs deploy (`dev`/`demo`).
- **Memory & retrieval:** long-term store (cross-session memory) · Knowledge agent (Vector Search) +
  side-by-side comparison · MLflow evaluation flywheel.
- **Advanced:** parallel gather + planner fan-out (`Send`) · HITL **edit + replan**.

## Conventions & do/don'ts

- **Build against contracts.** The state schema + I/O contracts are defined in
  [`agent_server/contracts.py`](agent_server/contracts.py); respect them — changing one is a team decision.
- **One stub/mock per agent** so each agent builds/tests in isolation.
- **Small, contract-respecting PRs.**
- **Keep `interrupt()` out of parallel steps.**
- **Auth:** on-behalf-of-user (OBO) / OAuth for Knowledge (Vector Search) + Genie (scope
  `dashboards.genie` — **not** the newer `genie`, which Apps don't support yet); the Operational
  agent + Lakebase use the app service principal. The Operational agent returns its generated SQL
  so the join is traceable and scorable.
- **Genie space:** a first-class DABs resource (`resources.genie_spaces` in `databricks.yml`),
  created from `data/genie/supply_chain.geniespace.json` and bound to the app on `bundle deploy` —
  no seed-then-patch. Two OBO steps the deploy *cannot* automate (security-gated): a workspace admin
  must enable the **Apps – On-Behalf-Of-User Authorization** Public Preview, and **each user accepts
  the OAuth consent on first open**. Until then the Analytics route degrades gracefully; every other route works.

## Skills

Read the relevant skill in `.agents/skills/` **before** executing a task. The Codex ports are
committed and derived from pinned sources — see [`.agents/skills/README.md`](.agents/skills/README.md)
and [`.agents/skills/UPSTREAM.md`](.agents/skills/UPSTREAM.md) (SHAs + refresh). Three upstream
sources plus repo-authored skills:
1. **Template build skills** (`quickstart`, `lakebase-setup`, `agent-memory`, `add-tools`,
   `deploy`, `run-locally`, …).
2. **Databricks Agent Skills** (selective, @ `00d0daf`): `databricks-core`, `databricks-lakebase`,
   `databricks-vector-search`, `databricks-dabs`, `databricks-apps`, `databricks-model-serving`.
3. **MLflow Skills** (selective, @ `b90eca1`): `instrumenting-with-mlflow-tracing`,
   `agent-evaluation`, `analyze-mlflow-trace`.
4. **Repo-authored:** `sync-architecture-docs`, `integration-test`.
Pull the non-vendored skills with `scripts/install-skills.sh`.

The `.agents/skills/` folders contain complete Codex workflows and resources; they do not depend on
`.claude/skills/` at runtime. When refreshing an upstream skill, preserve the Codex frontmatter,
tool terminology, and paths documented in `.agents/skills/UPSTREAM.md`.

## References

Links (template, skills, accelerator, docs): [`docs/references.md`](docs/references.md).
Architecture detail: [`docs/architecture.md`](docs/architecture.md). Storyboard & persona:
[`docs/storyboard.md`](docs/storyboard.md).

**Primary reference template (lean on it heavily):**
[`databricks/app-templates/agent-langgraph-advanced`](https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced).
