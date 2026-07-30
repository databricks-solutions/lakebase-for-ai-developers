# Codex skills

This repo ships complete, repo-local Codex skills with their scripts, examples, and references.
They are close ports of three pinned upstream sources plus two repo-authored skills; they do not
depend on `.claude/skills/` at runtime. Codex-specific frontmatter, tool terminology, and paths are
maintained here. Provenance, commit SHAs, and refresh steps are in [`UPSTREAM.md`](UPSTREAM.md).

## Project deploy notes / stale-skill flags (read before using the deploy/apps skills)

The upstream-derived skills are generic and pinned — they are refreshed from upstream, so keep
Codex-specific changes limited to the normalization documented in `UPSTREAM.md`. For *this repo*,
the canonical deploy guidance lives in
[`docs/DEPLOY.md`](../../docs/DEPLOY.md) + [`scripts/deploy.sh`](../../scripts/deploy.sh), not the
generic `deploy` skill. Specific deltas to keep in mind:

- **`deploy` skill — its "delete & recreate the app" path is the Lakebase orphan trap here.** This
  app's service principal owns Lakebase schemas; deleting the app destroys the SP and orphans them
  (`databricks_superuser` can't reassign — no `SET ROLE`). **Redeploy in place** (`make deploy` /
  `make redeploy`); if you must recreate, detach the Lakebase resource as `CAN MANAGE` first. See
  [`docs/lakebase-apps-permissions.md`](../../docs/lakebase-apps-permissions.md).
- **`databricks-apps` / `add-tools` skills predate the Apps-OBO Public-Preview requirement for Genie.**
  Genie via OBO needs a workspace admin to enable the **"Apps – On-Behalf-Of-User Authorization"**
  Public Preview *and* each user to accept the OAuth consent on first open — neither is automatable.
  Use scope `dashboards.genie` (not `genie`). Current steps: [`docs/DEPLOY.md`](../../docs/DEPLOY.md).
- **CLI floor is ≥ 0.295** for this repo's native `postgres` app resource (`deploy.sh` enforces it),
  not the 0.294 some skills mention.

## 1. Vendored template skills (in this directory)

Ported closely from
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

## 4. Repo-authored skills (NOT vendored)

Written for **this** repo — not copied from any upstream, so they are **never** overwritten by the
`UPSTREAM.md` refresh (the refresh `rsync`s only the named upstream skill dirs above). Edit these
freely; they have no pinned SHA.

| Skill | What it covers |
|-------|----------------|
| `sync-architecture-docs` | Keep `docs/architecture.md` (the 4 Mermaid diagrams) + the READMEs/agent instruction files in sync with the code; reconcile the known stale-doc patterns; verify with greps + Mermaid validity. Run after topology/tool/schema/auth/frontend changes. |
| `integration-test` | Drive the cold-start E2E deploy test (`scripts/integration_test.sh`): deploy to a throwaway Lakebase project + UC schema in an isolated git worktree, verify, and tear down safely. Use before a PR that touches `deploy.sh` / `databricks.yml` / the seed. |

---

See [`UPSTREAM.md`](UPSTREAM.md) for exact SHAs + refresh steps, `scripts/install-skills.sh`
for pulling the non-vendored skills, and [`docs/references.md`](../../docs/references.md) for all links.
