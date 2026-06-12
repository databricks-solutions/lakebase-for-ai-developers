# References

All code, demo, and doc links for the Supply-Chain Planner Copilot. Curated from the Google Doc
"Lakebase for the GenAI/ML Persona Storyboard" (all tabs + comments) and team research.

## Source document

- Storyboard doc: https://docs.google.com/document/d/1y1Owc67JArx4YPVp8tTskJ558IG1DvSlx2VKJHSDtWM/edit
- Demo scenarios / seen-UCOs source: https://docs.google.com/document/d/1_im4Vqk-oO9h9Me2gkTp1epVhzPGxD14NbJ30e-nS4M/edit

## Primary reference template (lean on heavily)

- **agent-langgraph-advanced** — LangGraph on Apps + Lakebase memory + MLflow + Next.js
  frontend, ships its own Claude Code skills:
  https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced
  - Frontend (cloned on demand by `start-app`): https://github.com/databricks/app-templates/tree/main/e2e-chatbot-app-next
  - Key files to copy/adapt: `agent_server/agent.py`, `agent_server/start_server.py`,
    `agent_server/utils_memory.py`, `databricks.yml`, `app.yaml`, `pyproject.toml`,
    `scripts/quickstart.py`, `scripts/start_app.py`, `AGENTS.md`.
- Sibling templates (other orchestration styles): https://github.com/databricks/app-templates

## Skills

- **Databricks Agent Skills** — https://github.com/databricks/databricks-agent-skills
  - **Vendored @ `00d0daf`** in `.claude/skills/`: `databricks-core`, `databricks-lakebase`,
    `databricks-vector-search`, `databricks-dabs`, `databricks-apps`, `databricks-model-serving`.
  - Not vendored (via `scripts/install-skills.sh`, `databricks aitools install`, CLI ≥ v1.0.0):
    `databricks-jobs`, `databricks-pipelines`, `databricks-serverless-migration`, and
    experimental `databricks-mlflow-evaluation`, `databricks-agent-bricks`, `databricks-ai-functions`.
- **MLflow Skills** — https://github.com/mlflow/skills
  - **Vendored @ `b90eca1`** in `.claude/skills/`: `instrumenting-with-mlflow-tracing`,
    `agent-evaluation`, `analyze-mlflow-trace`.
  - Not vendored (via `npx skills add mlflow/skills`): `analyze-mlflow-chat-session`,
    `retrieving-mlflow-traces`, `querying-mlflow-metrics`, `mlflow-onboarding`, `searching-mlflow-docs`.
  - See `.claude/skills/UPSTREAM.md` for SHAs + refresh.

## Accelerators & pgvector examples

- **Banking Agent Accelerator** — deterministic state-machine routing + async-checkpoint HITL
  (POST results into the LangGraph checkpoint, resume on next turn), Next.js frontend, Lakebase
  `AsyncCheckpointSaver`: https://github.com/databricks-industry-solutions/banking-agent-accelerator
- **Lakebase semantic search blog** (pgvector hybrid; the canonical how-to):
  https://community.databricks.com/t5/lakebase-blogs/how-to-perform-semantic-search-in-databricks-lakebase/ba-p/139846
- **AWS Aurora pgvector samples** (enablement-flow reference — embedding/index/query/RAG):
  https://github.com/aws-samples/aurora-postgresql-pgvector
- **Agno agentic RAG with pgvector**: https://docs.agno.com/knowledge/agents/agentic-rag-pgvector

## Databricks docs

### Lakebase (OLTP / Postgres)
- Overview: https://docs.databricks.com/aws/en/oltp/projects/about
- Register with Unity Catalog: https://docs.databricks.com/aws/en/oltp/projects/register-uc
- Synced Tables (reverse-ETL Delta→Lakebase; Snapshot/Triggered/Continuous):
  https://docs.databricks.com/aws/en/oltp/projects/sync-tables
- Connect (psycopg, pooling): https://docs.databricks.com/aws/en/oltp/projects/connect
- Postgres clients: https://docs.databricks.com/aws/en/oltp/projects/postgres-clients
- Autoscaling: https://docs.databricks.com/aws/en/oltp/projects/autoscaling
- Scale to zero: https://docs.databricks.com/aws/en/oltp/projects/scale-to-zero
- Create Postgres roles (`databricks_create_role`, manual GRANTs):
  https://docs.databricks.com/aws/en/oltp/projects/postgres-roles

### Apps + Lakebase (granting the App SP its DB access)
- **Our implemented design: [`lakebase-apps-permissions.md`](lakebase-apps-permissions.md)** — the
  hybrid we ship (native `postgres` app resource for role+CONNECT+CREATE; seed-job `grant_app_sp`
  task for SELECT on the synced `public` tables; SP self-creates its memory + write-back schemas),
  autoscaling ⇒ `postgres` key (not `database`), CLI ≥0.294, gotchas + dated citations.
- Add a Lakebase resource to a Databricks app: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/lakebase
- App resource types + privileges: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources
- DABs resources (`postgres` vs `database` keys): https://docs.databricks.com/aws/en/dev-tools/bundles/resources

### Agents, Genie, models
- Stateful agents (checkpointer/store on Lakebase): https://docs.databricks.com/aws/en/generative-ai/agent-framework/stateful-agents
- Agent Framework: https://docs.databricks.com/aws/en/generative-ai/agent-framework/
- Multi-agent Supervisor: https://docs.databricks.com/aws/en/generative-ai/agent-bricks/multi-agent-supervisor
- Mosaic AI Vector Search: https://docs.databricks.com/aws/en/generative-ai/vector-search
- Vector Search hybrid queries: https://docs.databricks.com/aws/en/vector-search/query-vector-search
- Genie Conversation API: https://docs.databricks.com/aws/en/genie/conversation-api
- `ai_query`: https://docs.databricks.com/aws/en/large-language-models/ai-query
- Query embedding models: https://docs.databricks.com/aws/en/machine-learning/model-serving/query-embedding-models

## Synthetic data

- dbldatagen (Databricks Labs): https://github.com/databrickslabs/dbldatagen
- dbldatagen API docs: https://github.com/databrickslabs/dbldatagen/blob/master/docs/source/APIDOCS.md
- Faker: https://github.com/joke2k/faker

## LangGraph

- PostgresSaver (checkpointer): https://reference.langchain.com/python/langgraph.checkpoint.postgres/PostgresSaver
- Add memory: https://docs.langchain.com/oss/python/langgraph/add-memory
- HITL with `interrupt()`: https://blog.langchain.com/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt/

## Internal (Databricks — auth required)

- FEIP-5984 (pgvector + VS): https://databricks.atlassian.net/browse/FEIP-5984
- Product Routing (positioning Databricks Vector vs Lakebase) — Confluence:
  https://databricks.atlassian.net/wiki/spaces/~7120200d4c393f21a14d67bba42ffb3e92a69f/pages/5892374578/Product+Routing
- `#apa-lakebase` Slack channel — combining pgvector with AI Search (open thread).
- Lakebase Level Up code references — *link TBD from team*.

## Open questions tracked
See [`../CLAUDE.md`](../CLAUDE.md#open-questions-need-resolution): Lakebase RLS, UC governance
scope over Postgres tables, pgvector freshness (embed-on-write vs Synced Tables), IVFFlat vs
HNSW, long-term memory scope.
