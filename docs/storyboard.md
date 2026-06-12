# Storyboard — Lakebase for the GenAI/ML Persona

A modular toolkit for account teams selling Lakebase to **GenAI/ML developer** personas. Pick
the scenarios that match the customer's pain. The Supply-Chain Planner Copilot demo in this repo
primarily proves **Scenarios 1 (Agentic State), 3 (Semantic Search), and 5 (Cost)**.

> **Canonical seeded scenario:** any IGBT / semiconductor-shortage narrative used in the pitch is
> illustrative; the demo's actual seeded data is a Henkel (SUP-001) / SKU-1001 adhesive quality
> issue (on-hand 40, open POs ~800, Q4 2026). See the README Tier 1/2/2b questions for the
> canonical test prompts.

> Source: Google Doc "Lakebase for the GenAI/ML Persona Storyboard" → *Tab 1* + *Supply Chain
> Planner Copilot* tab. **How we build it:** the agent-state and semantic-search scenarios run
> on the **LangGraph + Lakebase integration** in `databricks-langchain` (`AsyncCheckpointSaver`
> + `AsyncDatabricksStore`) — LangGraph manages the memory tables and the vector index; you do
> not hand-roll a pgvector client. See [`architecture.md`](architecture.md#state-memory--the-pgvector-path--langgraph--lakebase-integration).

## The persona

ML Engineers / AI Platform Engineers / Data Scientists who build and deploy **production** AI
applications — agents (retrieval, tool use, multi-step reasoning) and real-time ML models —
**including the operational data infrastructure** that backs them.

- **Roles:** Senior ML Engineer, GenAI Engineer, ML Platform Engineer, Applied AI Engineer.
- **Exclusions:** Data Scientists (notebook-based), Research Scientists (pre-production).
- **Today's stack (the sprawl):** Pinecone/Weaviate/Chroma/Qdrant (vectors); Redis/Mongo
  (agent state/cache); DynamoDB, Feast/Tecton (features); Elasticsearch (search); standalone
  Postgres (metadata); S3 (artifacts). 5+ credential sets.
- **What keeps them up at night:** agent memory scattered across 3+ systems; vector-DB lock-in
  with no governance; feature serving needs a separate real-time DB; every new app = new data
  stores; compliance asking "where does the agent store conversations?"; no clean loop to get
  prod data to DS for eval and results back into the app.

**Sell to this persona on the technical dimensions:** memory state, cost, latency, scalability,
I/O operations, custom embedding models, model improvement loop.

## Scenarios

### 1 · Agentic State (Agent Memory) — *demoed*
- **Pain:** "memory" is three things in three systems — short-term in Redis (ephemeral, lost on
  pod restart), long-term in ungoverned standalone Postgres, tool/checkpoint state in DynamoDB.
  Three DBs, three credential sets, three failure modes.
- **Demo moment:** one Lakebase project stores all three as Postgres tables, wired through
  LangGraph — `AsyncCheckpointSaver` for thread/checkpoint state and `AsyncDatabricksStore` for
  long-term memory; sub-ms reads on the hot path; Lakehouse Sync pushes conversation data to
  Delta for eval. Conversations survive pod restart (Postgres durability).
- **Key Lakebase feature:** Lakebase + LangGraph = durable, auditable agent state, checkpoints,
  and long-term memory in one Postgres backend — replacing Redis + standalone Postgres + DynamoDB.

### 2 · Feature Store (Feature Serving)
- **Pain:** model serves in 50ms but features live in a batch table (200–500ms point lookups);
  team copied features to Redis → two copies, drift, served stale features for 3 days.
- **Demo moment:** features computed in lakehouse → **Synced Table (continuous)** into Lakebase
  → sub-100ms point lookups from Model Serving via psycopg. One logical copy, fresh in seconds.
- **Key Lakebase feature:** Synced tables serve lakehouse-computed features at Postgres speed.

### 3 · Semantic Search — *demoed*
- **Pain:** the agent's retrieval tool can't see operational data. External vector DB → two
  round trips stitched in code, no SQL join to permissions; lakehouse VS → still a round trip,
  no operational joins at serve time.
- **Demo moment:** `AsyncDatabricksStore` gives the agent semantic search over memory — LangGraph
  manages the Postgres vector tables/index, embeddings generated through a Databricks embedding
  endpoint (configured on the store; **not** `ai_query`). Operational tables reach Lakebase via
  Synced Tables, so the Operational agent runs **one** governed SQL: vector similarity + access
  predicate + SQL joins to inventory/open POs. Complements (not replaces) Vector Search; replaces
  external vector DBs.
- **Key Lakebase feature:** semantic memory + operational joins in one Postgres backend —
  similarity is one predicate inside a relational query, no separate vector service.

### 4 · Model Serving
- **Pain:** predictions land in the lakehouse, app data in another DB → slow round trip or a
  custom copy pipeline; can't combine predictions + operational data in one request.
- **Demo moment:** Model Serving → Delta → **Synced Table** → Lakebase; app queries predictions
  + operational data in one <100ms Postgres call; reverse: Lakehouse Sync pushes new app data
  back for retraining + MLflow eval.
- **Key Lakebase feature:** bidirectional Synced/Lakehouse Sync — no custom pipelines.

### 5 · Cost — *demoed*
- **Pain:** separate vector DB + standalone Postgres run 24/7 at provisioned capacity; every
  new app = another provisioned DB; dev/staging idle most of the time.
- **Demo moment:** **scale-to-zero** dev/staging (= $0 idle), autoscaling in prod, new
  workloads add **tables** to an existing project — not new services.
- **Key Lakebase feature:** autoscaling + scale-to-zero. Pay for what you use.

## Demo assets (from the doc appendix)
Agent Memory (short/long-term notebooks, agent GitHub demo, Loom) · Feature Serving (feature
store Lakebase notebook, online feature store docs, Vocareum lab, demo catalog) · Semantic
Search (agent GitHub demo with pgvector patterns, demo catalog, Loom, semantic-search blog) ·
Model Serving (feature store notebook serving patterns, demo catalog, Vocareum) · Cost (pricing
doc, spend analytics decks). Links in [`references.md`](references.md).
