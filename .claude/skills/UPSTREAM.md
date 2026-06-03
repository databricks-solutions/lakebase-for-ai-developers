# Vendored skills — provenance & refresh

Skills in this directory come from three upstream sources, copied in (vendored) so they're
**repo-local, committed, and loadable for everyone** without a per-developer install step.
Vendored copies **drift** from upstream — refresh before the demo (see below).

## Source 1 — agent-langgraph-advanced template (build skills)

- **Repo:** https://github.com/databricks/app-templates → `agent-langgraph-advanced/.claude/skills`
- **Skills:** `quickstart`, `lakebase-setup`, `agent-memory`, `discover-tools`, `create-tools`,
  `add-tools`, `deploy`, `run-locally`, `modify-agent`, `load-testing`, `migrate-from-model-serving`
- Copied verbatim (SKILL.md + `examples/`). These assume the template's `agent_server/` layout
  that WS1 will bring in.

## Source 2 — Databricks Agent Skills (selective)

- **Repo:** https://github.com/databricks/databricks-agent-skills
- **Commit pinned:** `00d0daf801b4ed82ead6868d119c36f518333907`
- **Skills vendored:** `databricks-core` (parent of the others), `databricks-lakebase`,
  `databricks-vector-search`, `databricks-dabs`, `databricks-apps`, `databricks-model-serving`
- Copied `SKILL.md` + `references/` only (excluded `assets/` icons and `agents/` marketplace
  metadata). `databricks-core` is included because the others declare `parent: databricks-core`.
- **CLI compatibility:** the skills' CLI commands require **Databricks CLI ≥ v0.294.0**; the
  `databricks aitools install` mechanism (used for the full set) requires **≥ v1.0.0**.
- *Not vendored* (available via `scripts/install-skills.sh`): `databricks-jobs`,
  `databricks-pipelines`, `databricks-serverless-migration`, and all `experimental/` skills
  (e.g. `databricks-mlflow-evaluation`, `databricks-agent-bricks`).

## Source 3 — MLflow Skills (selective)

- **Repo:** https://github.com/mlflow/skills
- **Commit pinned:** `b90eca153a902ed69bc6339130e314fb0632abc7`
- **Skills vendored:** `instrumenting-with-mlflow-tracing`, `agent-evaluation`,
  `analyze-mlflow-trace`
- Copied `SKILL.md` + `references/` + `scripts/` (excluded `assets/`).
- *Not vendored* (available via `npx skills add mlflow/skills`): `analyze-mlflow-chat-session`,
  `retrieving-mlflow-traces`, `querying-mlflow-metrics`, `mlflow-onboarding`,
  `searching-mlflow-docs`, `mlflow-agent`.

## Refresh procedure

To re-pull the latest upstream versions (and update the SHAs above):

```bash
# Databricks Agent Skills
git clone --depth 1 --filter=blob:none --sparse https://github.com/databricks/databricks-agent-skills /tmp/das
( cd /tmp/das && git sparse-checkout set skills && git rev-parse HEAD )   # record new SHA
for s in databricks-core databricks-lakebase databricks-vector-search databricks-dabs databricks-apps databricks-model-serving; do
  rsync -a --delete --exclude 'assets/' --exclude 'agents/' /tmp/das/skills/$s/ .claude/skills/$s/
done

# MLflow Skills
git clone --depth 1 https://github.com/mlflow/skills /tmp/mfs
( cd /tmp/mfs && git rev-parse HEAD )                                     # record new SHA
for s in instrumenting-with-mlflow-tracing agent-evaluation analyze-mlflow-trace; do
  rsync -a --delete --exclude 'assets/' /tmp/mfs/$s/ .claude/skills/$s/
done
```

Then update the two pinned SHAs in this file.
