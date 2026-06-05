# CLAUDE.md — Supply-Chain Planner Copilot

> Shared context for every engineer's Claude Code sessions. Read this first so sessions stay
> aligned on the use case, stack, environment, and architecture. The shared state schema and
> I/O contracts are a Phase-0 task — agree any interface in a PR until they land.

## Project & goal

A multi-agent **Supply-Chain Planner Copilot** for manufacturing planners. A planner asks a
question; the system routes it to the right engine, produces a recommendation with
**human-in-the-loop approval**, remembers prior sessions, and is traced end-to-end with MLflow.

It is a demo that shows how to build a **stateful agent on Databricks with Lakebase** — agent
memory, session/tool state, and operational semantic retrieval in one Postgres backend — and
when to use **Lakebase + LangGraph** vs **Mosaic AI Vector Search**.

**Canonical demo scenario:** *"Show me similar quality issues for this supplier, scoped to the
product codes I can access, joined to on-hand inventory and open POs"* → recommendation → gate
→ HITL approval → commit, visible as one MLflow trace and resumable from the Lakebase checkpoint
across the pause.

## Architecture

LangGraph on **Databricks Apps** (not Model Serving — Apps runs the graph in-process as
background work with the UI polling state; the durable checkpoint makes a run resumable across
app restarts). **All durable state lives on Lakebase (Postgres).** MLflow autologs traces.

```
Supervisor (router) → parallel Gather → Planner (fan-out per SKU/supplier) → Aggregate + Gate → [HITL interrupt()] → Commit
```

The **gather phase** has three sibling retrieval/data agents:

- **Operational — Lakebase.** Semantic similarity as *one predicate* in a governed relational
  query that joins live operational rows (inventory, open POs, access scope) in a single SQL
  statement. Output: rows with operational context.
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
  directly so vector similarity + SQL joins + access scope resolve in one governed statement —
  rather than pulling IDs from a vector index and round-tripping to Postgres to join and re-filter.

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
- **Frontend:** Next.js (`e2e-chatbot-app-next`, cloned on demand by `start-app`).

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

## Repo layout & workstreams

| Path | Workstream / owner | Holds |
|------|--------------------|-------|
| `agent_server/` | **WS1** Spine, App & DABs (Alex & Kylie) + **WS5** LangGraph/Routing (Ram) | Graph skeleton, supervisor routing, checkpointer+store wiring, MLflow autolog, App run/poll/resume handlers |
| `data/` | **WS2** Operational (Chandhana) + **WS4** demo data | Synthetic data gen, operational tables, the hybrid similarity+join query; seeded demo scenario |
| `agent_server/` (Genie tool) | **WS3** Genie + (stretch) Vector Search (Ram/Kylie) | Genie space wrapper; VS Knowledge agent |
| `agent_server/` (planner/HITL) | **WS4** Planner + HITL + demo | Planner node, gate/threshold, `interrupt()` approval card, run-of-show |
| `databricks.yml` | **WS1** | DABs bundle: App resource, Lakebase, experiment, setup/seed job, `dev`/`demo` targets |
| `docs/` | all | Architecture, storyboard, sprint plan, references |
| `.env.example` | all | Local config template (`cp` → `.env`) — see auth section above |
| `.claude/skills/` | all | Vendored, pinned build + Databricks + MLflow skills |

## Scope tiers (build P0 first, iterate)

- **P0 — core loop:** supervisor + routing · Operational agent (hybrid similarity + join to
  on-hand/POs) · Genie Analytics agent · **sequential** planner + gate · short-term checkpointer ·
  HITL approve/reject via `interrupt()` · MLflow autolog · Databricks App (run/poll/resume) ·
  seeded demo dataset · OBO auth · DABs deploy (`dev`/`demo`).
- **P1:** long-term store (cross-session memory) · Knowledge agent (Vector Search) +
  side-by-side comparison.
- **P2:** parallel gather + planner fan-out (`Send`) · HITL **edit + replan** · MLflow evaluation.

## Conventions & do/don'ts

- **Build against contracts.** Once the state schema + I/O contracts land (Phase 0), respect
  them; changing one is a team decision. Until then, agree interfaces in the PR.
- **One stub/mock per agent** so workstreams build/test in isolation.
- **Small, contract-respecting PRs.**
- **Keep `interrupt()` out of parallel steps.**
- **Auth:** on-behalf-of-user (OBO) / OAuth. The Operational agent returns its generated SQL so
  the join and access scope are traceable and scorable.
- **DABs carve-out:** the Genie space is not a clean DABs resource — create it manually and
  reference it as an app resource.

## Skills

Read the relevant skill in `.claude/skills/` **before** executing a task. All skills are
vendored (committed) and pinned — see [`.claude/skills/README.md`](.claude/skills/README.md) and
[`.claude/skills/UPSTREAM.md`](.claude/skills/UPSTREAM.md) (SHAs + refresh). Three sources:
1. **Template build skills** (`quickstart`, `lakebase-setup`, `agent-memory`, `add-tools`,
   `deploy`, `run-locally`, …).
2. **Databricks Agent Skills** (selective, @ `00d0daf`): `databricks-core`, `databricks-lakebase`,
   `databricks-vector-search`, `databricks-dabs`, `databricks-apps`, `databricks-model-serving`.
3. **MLflow Skills** (selective, @ `b90eca1`): `instrumenting-with-mlflow-tracing`,
   `agent-evaluation`, `analyze-mlflow-trace`.
Pull the non-vendored skills with `scripts/install-skills.sh`.

## References

Links (template, skills, accelerator, docs): [`docs/references.md`](docs/references.md).
Architecture detail: [`docs/architecture.md`](docs/architecture.md). Storyboard & persona:
[`docs/storyboard.md`](docs/storyboard.md). Sprint plan: [`docs/sprint-plan.md`](docs/sprint-plan.md).

**Primary reference template (lean on it heavily):**
[`databricks/app-templates/agent-langgraph-advanced`](https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced).
