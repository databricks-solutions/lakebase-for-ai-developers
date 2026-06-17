# Lakebase state lifecycle — dev → staging → prod

> How the agent's durable state (LangGraph checkpointer + long-term/semantic store + Meridian
> write-back) is isolated per environment and promoted between them. For the deep permission/ownership
> mechanics see [`lakebase-apps-permissions.md`](lakebase-apps-permissions.md); for the deploy flow see
> [`DEPLOY.md`](DEPLOY.md).

## The model: one project, branch-per-environment

All durable state lives on **Lakebase (Postgres)**. We run **one autoscaling Lakebase project** and
give **each environment its own copy-on-write branch** — not a separate project per environment. The
branch is the isolation boundary: dev, staging, and prod each read and write their own copy of the
agent state, and lower branches are forked from `production`.

- **Agent state** (checkpointer, store/`store_vectors`, write-back) — isolated per branch.
- **Operational data** (the `public` synced tables) — a prod-shaped snapshot forked onto each branch.
- **Analytics** (UC catalogs/schemas) — a separate concern, set per-tier via `uc_catalog`/`uc_schema`.

Lakebase is workspace-scoped, so this assumes one workspace. A locked-down prod in its own workspace
would use a separate project (point `lakebase_project` at it per-target).

## Topology

```mermaid
flowchart TB
    subgraph WS["Single workspace · one Lakebase project: mfg-supply-chain-copilot"]
        PROD["production<br/>(default branch)<br/>canonical state"]
        STG["staging"]
        DEV["development<br/>(shared dev)"]
        DEMO["demo<br/>(showcase)"]
        EPH["dev-{name} · ci-pr-{n}<br/>(ephemeral · planned)"]
        PROD -. "fork · copy-on-write" .-> STG
        PROD -.-> DEV
        PROD -.-> DEMO
        PROD -.-> EPH
    end

    A1["target prod<br/>app: supply-chain-planner"] ==> PROD
    A2["target staging<br/>app: supply-chain-planner-staging"] ==> STG
    A3["target dev<br/>app: {user}-supply-chain-planner"] ==> DEV
    A4["Local IDE<br/>your U2M identity"] ==> DEV
```

Solid arrows = a DABs target/app deploys to and reads+writes that branch; dotted arrows =
copy-on-write fork from `production`.

| Tier | DABs target | Lakebase branch | App name | Agent-state schemas |
|------|-------------|-----------------|----------|---------------------|
| Local IDE | — | `development` (via `.env`) | — | `dev_<you>_memory` / `_app` |
| Dev | `dev` (default) | `development` | `<user>`-prefixed | `dev_memory` / `dev_app` |
| Staging | `staging` | `staging` | `supply-chain-planner-staging` | `staging_memory` / `staging_app` |
| Prod | `prod` | `production` | `supply-chain-planner` | `supply_chain_planner_memory` / `_app` |
| Demo (legacy) | `demo` | `production` | `supply-chain-planner` | canonical |
| BYO (restricted ws) | `byo` | `production` | `<user>`-prefixed | canonical |

`production` is the project's **default branch** (created with the project); the others are **forked
from it** copy-on-write by the deploy script the first time you deploy that tier. `demo` is a showcase
alias of `prod`; use `prod` for the lifecycle. `byo` targets a restricted workspace where you bring an
existing SQL warehouse.

## Why agent-state schema names differ per tier

Prod uses the canonical schema names (`supply_chain_planner_memory` / `_app`) — it created them, so it
owns them. But a branch forked from prod inherits those schemas **still owned by the prod app service
principal**, and on Lakebase nobody (not even the deployer) can drop or reassign them — there's no
usable superuser. A different-SP tier (dev/staging) trying to reuse the canonical name would crash on
startup with "permission denied."

So rather than fight it, each non-prod tier uses its **own** schema names (`staging_memory`,
`dev_memory`, …). The tier's app SP creates and owns those fresh on first boot; the inherited prod
schemas just sit there unused. (`public` is platform-owned, so it needs no rename — the seed's
`grant_app_sp` task just re-grants the tier SP read access.)

> Caveat: the background-response store uses a hard-coded schema name (`agent_server`) we can't
> rename, so on non-prod tiers it stays prod-owned and **background mode degrades** — non-fatal; the
> normal invoke/stream, agent memory, and write-back paths all work. Full mechanics:
> [`lakebase-apps-permissions.md`](lakebase-apps-permissions.md).

## Promotion: code goes up, data goes down

```mermaid
flowchart LR
    PROD["production<br/>(canonical state · source of truth)"]
    LOWER["staging · development · ephemeral"]
    PROD ==>|"data down: fork / reset — instant, copy-on-write"| LOWER
    LOWER -. "memory up: distillation job — curated rows only (planned)" .-> PROD
```

The two flows move in opposite directions:

- **Code & schema promote up** (dev → staging → prod) via DABs deploy plus idempotent DDL (the
  `ensure_*` startup functions use `CREATE … IF NOT EXISTS`).
- **Data & memory flow down** (prod → staging/dev) by forking or resetting from `production` — instant
  and free. There is **no** child→parent merge: canonical memory lives on `production`, lower branches
  are throwaway, and curated memory is promoted up later by a distillation job (planned).

## Environment awareness

The DABs target is surfaced to the app as **`APP_ENV`** (read as `settings.app_env`) and stamped on
every MLflow trace as `environment=dev|staging|prod`, so traffic is separable in observability.
Defaults to `local` for IDE runs.

## Local development

Run locally against the `development` branch with **your own** schema names — never the app SP's. This
is the same tier-named-schema idea as the deployed tiers, where the "tier" is you (it also means a
local run can't corrupt shared state):

```
LAKEBASE_AUTOSCALING_BRANCH=development
LAKEBASE_AGENT_MEMORY_SCHEMA=dev_<you>_memory
LAKEBASE_WRITEBACK_SCHEMA=dev_<you>_app
```

## Operational guardrails

- **Never delete the app — redeploy in place.** The app SP owns its Lakebase schemas and is destroyed
  on app delete, orphaning them. If you must recreate, detach the Lakebase resource as `CAN MANAGE`
  first. See [`lakebase-apps-permissions.md`](lakebase-apps-permissions.md).
- **Grants survive redeploys** (they're app-resource bindings), but `SELECT` on the `public` synced
  tables comes from the seed's `grant_app_sp` task — re-run the seed after an SP recreate.
- **Branch limit:** 10 unarchived branches per project — clean up stale `dev-*` / `ci-*` branches.

## Roadmap

**Done:** branch-per-environment (`dev`/`staging`/`prod` targets) · tier-named agent-state schemas ·
automated branch provisioning (forks from prod if missing, fails loud otherwise) · `APP_ENV` on traces.

**Planned:** per-developer / per-PR ephemeral branches with a TTL reaper · CI/CD regression gate on
PRs (ephemeral branch → flywheel) · scheduled staging reset-from-prod + memory distillation ·
per-branch state monitoring.

## Usage

```bash
make deploy PROFILE=<p> TARGET=dev       # → development branch (default)
make deploy PROFILE=<p> TARGET=staging   # → staging branch (forked from production if missing)
make deploy PROFILE=<p> TARGET=prod      # → production branch

# Per-developer isolation: your own branch, or your own schema on the shared branch
make deploy PROFILE=<p> TARGET=dev --var lakebase_branch=dev-<you>
make deploy PROFILE=<p> TARGET=dev --var lakebase_agent_memory_schema=dev_<you>_memory \
                                   --var lakebase_writeback_schema=dev_<you>_app
```
