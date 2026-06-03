# Architecture — Supply-Chain Planner (Multi-Agent)

A multi-agent planning system on **Databricks Apps** that decomposes a planning request across
specialized agents, produces recommendations with **human-in-the-loop approval**, and is
instrumented end-to-end with **MLflow**. Built on **LangGraph** with all durable state on
**Lakebase**. It shows when to use **Lakebase + LangGraph** vs **Mosaic AI Vector Search**.

> Source: Google Doc "Lakebase for the GenAI/ML Persona Storyboard" (Architecture + Copilot
> tabs), refined to the **Databricks-native LangGraph + Lakebase integration** (see below).

## Topology

```
Supervisor (router)
   → parallel Gather
        ├─ Operational  (Lakebase: similarity + SQL joins) — rows + operational context
        ├─ Knowledge    (Mosaic AI Vector Search)          — passages for grounding
        └─ Analytics    (Genie / NL→SQL)                   — structured aggregation
   → Planner (fan-out per SKU/supplier)
   → Aggregate + Gate (threshold → needs_approval / est_cost)
   → [HITL interrupt()]  (approve / reject / — later — edit + replan)
   → Commit  (write decision; update long-term memory)
```

## Key decisions

- **LangGraph over OpenAI Agents SDK.** Lakebase is Postgres, so the checkpointer and long-term
  store come essentially free; `interrupt()` gives durable HITL; MLflow autologs LangGraph traces.
- **Databricks Apps, not Model Serving.** Planning runs execute in-process as background work
  with the UI polling state — Apps times out long synchronous requests, and the durable
  checkpoint makes a run resumable across app restarts.
- **On-behalf-of-user (OBO) auth** for Genie. The Operational agent enforces access scope as a
  predicate inside its SQL (e.g. `product_code IN planner_acl`).

## State, memory & the pgvector path — LangGraph + Lakebase integration

**The Lakebase + LangGraph integration lives in `databricks-langchain` and manages the tables,
connection pooling, and vector index for you. You do not hand-manage a pgvector client.**

- **Short-term — `AsyncCheckpointSaver`.** Thread/session state and checkpoints, one thread per
  planning session. The whole state snapshots every superstep; per-task pending writes mean a
  partial-failure resume doesn't re-run already-succeeded gather branches (no recompute of
  expensive Genie / store / Vector Search calls). HITL `interrupt()` resumes from here.
- **Long-term + semantic — `AsyncDatabricksStore`.** A Lakebase-backed LangGraph Postgres store
  with embeddings configured via a Databricks **embedding endpoint** (`embedding_endpoint`,
  `embedding_dims`, `embedding_fields`). **This is the pgvector path** — internally it builds an
  index config and an `AsyncPostgresStore(conn, index=...)`, persisting memory + vectors in
  Postgres tables (`store`, `store_vectors`, `store_migrations`, `vector_migrations`) and serving
  semantic search over stored memory. Namespaced by `(planner, entity)`: preferences, prior
  approvals, learned supplier notes; hydrated at run start, written at commit.
- **Connections & creds.** Both wrappers use a managed `LakebasePool` / `AsyncLakebasePool` and
  generate short-lived OAuth DB credentials via the Databricks SDK (no hard-coded secrets). In
  autoscaling mode the SDK resolves the branch's read-write endpoint/host.
- **Embeddings are configured on the store**, generated at write/search time through the endpoint
  — **not** via `ai_query` (which is irrelevant to the Lakebase storage path here).

```python
from databricks.sdk import WorkspaceClient
from databricks_langchain import AsyncCheckpointSaver, AsyncDatabricksStore, ChatDatabricks
from langchain.agents import create_agent

w = WorkspaceClient()
checkpointer = AsyncCheckpointSaver(project=PROJECT, branch=BRANCH, workspace_client=w, schema=SCHEMA)
store = AsyncDatabricksStore(
    project=PROJECT, branch=BRANCH, workspace_client=w, schema=SCHEMA,
    embedding_endpoint="databricks-gte-large-en", embedding_dims=1024, embedding_fields=["$"],
)
await store.setup()  # creates schema + store/store_vectors/migration tables
agent = create_agent(model=ChatDatabricks(endpoint=LLM_ENDPOINT), tools=[...],
                     checkpointer=checkpointer, store=store, state_schema=AgentState)
```

**Mental model:** checkpointer = "what happened in this thread?"; `DatabricksStore` = "what should
the agent remember across threads, and what semantically similar memories should it retrieve now?"

### LangGraph state discipline
Gather agents write **distinct** state keys (no reducer); the planner fan-out writes the shared
`plans` key (reducer required). Bulk payloads go to side tables; state holds references (every
superstep serializes the full snapshot).

## The Operational agent — similarity + SQL joins in one query

The differentiator: when a question needs **semantic similarity as one predicate inside a
relational/operational query**, run it against the same Lakebase backend so similarity + joins +
access scope resolve in ONE governed SQL statement — instead of pulling IDs from a vector index
and round-tripping to Postgres to join, re-filter, and re-check access.

**Canonical case.** *"Similar quality issues for this supplier, scoped to product codes I can
access, joined to on-hand inventory and open POs."*

```sql
SELECT m.summary, m.supplier_id, m.sku, i.on_hand_qty, po.open_po_qty,
       1 - (m.embedding <=> %(query_embedding)s) AS similarity
FROM agent_memory m
JOIN inventory i  ON m.sku = i.sku
JOIN open_pos  po ON m.supplier_id = po.supplier_id AND m.sku = po.sku
JOIN user_access ua ON ua.product_code = m.product_code
WHERE ua.user_id = %(user_id)s AND m.expired_at IS NULL
ORDER BY m.embedding <=> %(query_embedding)s
LIMIT 5;
```

Operational tables (inventory, open POs, supplier status) reach Lakebase via **Synced Tables**
(Continuous for fast tables, Snapshot for slow dims) so the joins hit fresh OLTP rows.

## Lakebase vs Mosaic AI Vector Search

| Dimension | Mosaic AI Vector Search | Lakebase (LangGraph store + SQL) |
|---|---|---|
| Data nature | large unstructured knowledge corpus | agent memory + operational records co-located with entities |
| Vector storage | managed index over Delta | LangGraph `DatabricksStore` (Postgres `store_vectors`) |
| Operational join | app-side, multi-hop | native SQL join, single query |
| Row-level access | index filters / app-side | in-query predicate / Postgres grants |
| Scale | large corpora (100Ks–>100M), managed HNSW | small-to-moderate sets co-located with state |
| Managed RAG features | ingestion, hybrid retrieval, reranking | not built-in |
| Output | passages | rows + operational context |

**Routing:**
- **Lakebase + LangGraph** when the agent needs memory + semantic similarity + live SQL joins in
  one operational query path.
- **Mosaic AI Vector Search** for large-scale managed RAG over broad document corpora (managed
  ingestion, hybrid retrieval, reranking, large vector counts).
- **Genie** for structured business questions (*"total unfulfilled demand by product code for Q4/Q1?"*).

## Human-in-the-loop

`interrupt()` in a dedicated review node **after** fan-in, kept out of any parallel step so a
resume doesn't drag sibling tasks into re-execution. The run pauses durably; the planner can
re-engage on an edit; the approve/edit/reject verdict feeds long-term memory. The App uses a
**two-call resume pattern** around the pause.

## Models & observability

- **Models:** `ChatDatabricks` against Databricks Foundation Model APIs — fast model for the
  router, mid-tier for retrieval + Genie-wrapper agents, strongest model for the planner. MLflow
  tags model-per-span to attribute cost/latency/quality by layer.
- **Observability:** MLflow tracing autologged across nodes (span, model, cost, latency). Eval
  dimensions: routing correctness, retrieval relevance, operational SQL/join correctness,
  recommendation quality, escalation correctness. The Operational agent **returns its generated
  SQL** so the join and access scope are traceable and scorable.

## Helpful resources

- Stateful agents: https://docs.databricks.com/aws/en/generative-ai/agent-framework/stateful-agents
- Reference template: https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced
- Databricks agent skills: https://github.com/databricks/databricks-agent-skills
- MLflow skills: https://github.com/mlflow/skills
- Banking agent accelerator (state-machine + async-checkpoint HITL): https://github.com/databricks-industry-solutions/banking-agent-accelerator
