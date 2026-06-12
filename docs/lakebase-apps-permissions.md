# Lakebase + Databricks Apps — granting the App service principal its database access

> **Implemented design** (mid-2026). This is how the App's service principal (SP) gets its Lakebase
> access in this repo — a native `postgres` app resource for role + CONNECT + CREATE, plus a
> seed-job SELECT grant for the synced operational tables. Validated against product/eng docs,
> Slack, and Glean, and now wired in [`databricks.yml`](../databricks.yml),
> [`data/operational/05_grant_app_sp.py`](../data/operational/05_grant_app_sp.py), and the app's
> startup self-create. The dated citations + the skill-vs-ground-truth `database`-field note below
> are kept so the design stays auditable.

---

## TL;DR

The permission model is a **hybrid** — the platform does the role + CREATE; the seed does the
SELECT:

1. **A native `postgres` app resource** in `databricks.yml` makes the platform register the SP's
   Postgres role and grant it `CONNECT` + `CREATE` on the database (`CAN_CONNECT_AND_CREATE`). That
   is enough for the LangGraph checkpointer + memory store **and** the Meridian write-back tables to
   **create and own their own schemas** at startup — no hand-registered role, no `GRANT CREATE ON
   DATABASE`.
2. **A seed-job task `GRANT`s `USAGE` + `SELECT`** on the operational synced-tables schema
   (`public`) — the platform **never** auto-grants SELECT on tables the SP didn't create, so this
   one grant genuinely stays in the seed job, run as a Lakebase branch superuser.

So the resource subsumes the old REST role-registration and the old `GRANT CREATE ON DATABASE`
step; only the **operational-schema SELECT/USAGE grant** remains. Two hard requirements: the
**`postgres`** resource key (autoscaling) — *not* `database` (classic only) — and **Databricks CLI
≥ v0.294**.

---

## What the repo does

The app's `resources:` block in [`databricks.yml`](../databricks.yml) declares the MLflow
`experiment` (`CAN_MANAGE`) **and** a native `postgres` Lakebase resource
(`CAN_CONNECT_AND_CREATE`). Lakebase here is **autoscaling** (projects / branches / endpoints), not
classic "database instances," so the `postgres` key is the correct one.

```yaml
resources:
  - name: 'experiment'
    experiment: { experiment_id: ${resources.experiments.planner_experiment.id}, permission: 'CAN_MANAGE' }
  - name: lakebase
    postgres:
      branch: projects/${var.lakebase_project}/branches/${var.lakebase_branch}
      database: projects/${var.lakebase_project}/branches/${var.lakebase_branch}/databases/${var.lakebase_database_resource}
      permission: CAN_CONNECT_AND_CREATE
```

On deploy the platform registers the SP's Postgres role (role name == its client-id UUID) and
grants it `CONNECT` + `CREATE` on the database. The seed-job task
[`data/operational/05_grant_app_sp.py`](../data/operational/05_grant_app_sp.py) (task key
`grant_app_sp`, depends on `sync_to_lakebase`) then runs as the Lakebase branch superuser and does
the **one** thing the resource can't: `GRANT USAGE` + `SELECT ON ALL TABLES` on the operational
`public` schema (where the synced read tables — `inventory_current`, `open_pos`, `suppliers`,
`quality_incidents`, … — live, owned by the platform's `databricks_writer_*` role). Because
`make deploy` deploys the app **before** the seed runs, the SP already exists when the grant fires.

The app's connection code keys off the platform-injected Lakebase env
([`data/operational/_lakebase.py`](../data/operational/_lakebase.py) Pattern 2 uses `PGHOST` +
`LAKEBASE_ENDPOINT`), so the native resource plugs straight in — no connection rewrite. At startup
the SP **self-creates** the two schemas it owns (it has CREATE on the database):
[`agent_server/operational_db.py`](../agent_server/operational_db.py) `ensure_memory_schema()`
(the LangGraph checkpoint + store schema) and `ensure_writeback_tables()` (the SP-owned write-back
schema + its three Meridian tables).

---

## Ground truth

### 1. The native Apps Lakebase resource auto-grants `CONNECT` + `CREATE` — and nothing more

Adding a Lakebase database as an app resource creates a Postgres role **named after the SP's client
id** and grants it `CONNECT` + `CREATE` on the database (permission label
`CAN_CONNECT_AND_CREATE`). It does **not** grant schema `USAGE` or table-level `SELECT`/DML.

- Docs — *Add a Lakebase resource to a Databricks app* (2026‑05‑22): *"Databricks creates a
  PostgreSQL role in the selected database. The role name matches the service principal's client
  id"* and *"grants the service principal `CONNECT` and `CREATE` privileges on the selected
  database."* `https://docs.databricks.com/aws/en/dev-tools/databricks-apps/lakebase`
- Slack — **theo.fernandez (Apps eng)**, #apa-apps, 2025‑07‑07: *"Apps will only grant the postgres
  CONNECT and CREATE permissions to the service principal's role… If you want to grant additional
  permissions to the role"* you do it yourself.
- Slack — **phil.sheffield (Lakebase)**, #apa-lakebase, 2026‑06‑02: *"Right, no Lakebase-specific
  auto-grant."*

**Confidence: High.** The "CONNECT + CREATE only" boundary is stated identically across docs + two
eng sources.

### 2. Autoscaling vs classic — the decisive point: use the `postgres` key, CLI ≥ v0.294

DABs exposes **two** Lakebase app-resource keys:

| Lakebase shape | Resource key | Key fields | Permission |
|---|---|---|---|
| **Autoscaling** (projects/branches) | **`postgres`** | `branch`, `database` (full `projects/…/branches/…/databases/…` paths) | `CAN_CONNECT_AND_CREATE` |
| Provisioned / classic (instances) | `database` | `instance_name`, `database_name` | `CAN_CONNECT_AND_CREATE` |

Our Lakebase is **autoscaling**, so the correct key is **`postgres`**. The `database` key resolves
**only** Provisioned instances (it queries `/api/2.0/database/instances`, not
`/api/2.0/postgres/projects`).

- Slack — **tushar.madan**, #help-dabs, 2026‑03‑29 (self-resolved, upvoted): *"The correct resource
  type for Autoscaling Lakebase in databricks.yml is `postgres`, not `database`. You also need CLI
  v0.294+ (otherwise `postgres` is treated as an unknown field). Key gotcha: the `database` field
  needs the internal database resource name, not the Postgres database name."*
- Docs — *DABs resources* (2026‑06‑09) documents both keys + fields:
  `https://docs.databricks.com/aws/en/dev-tools/bundles/resources`
- Docs — *Upgrade to Autoscaling*: new Lakebase instances are created as **autoscaling projects**
  since **2026‑03‑12** (classic is legacy): `https://docs.databricks.com/aws/en/oltp/upgrade-to-autoscaling`

Our block (binds to an **already-existing** project/branch/database — see `databricks.yml`):

```yaml
resources:
  apps:
    supply_chain_planner:
      resources:
        - name: experiment
          experiment: { experiment_id: ${resources.experiments.planner_experiment.id}, permission: CAN_MANAGE }
        - name: lakebase
          postgres:
            branch: projects/${var.lakebase_project}/branches/${var.lakebase_branch}
            database: projects/${var.lakebase_project}/branches/${var.lakebase_branch}/databases/${var.lakebase_database_resource}
            permission: CAN_CONNECT_AND_CREATE
```

> **Skill-vs-ground-truth — the `database` field.** The vendored skills show the `database` field
> taking an **opaque** internal id (`db-xxxx`). That is **stale** — trust the ground truth: the field
> wants the **internal database resource name**, and as of 2026 it's **deterministic**: the
> kebab-cased dbname (db `databricks_postgres` → `databricks-postgres`). We pin it as the
> `lakebase_database_resource` bundle variable (default `databricks-postgres`). **Always verify** on
> the branch before trusting either: `databricks api get
> /api/2.0/postgres/projects/<p>/branches/<b>/databases`.

**Caveat — one-bundle bootstrap not possible yet.** You **cannot** create the autoscaling project +
database **and** bind it to the app in a single bundle: there's no declarative `postgres_databases`
/ `postgres_synced_tables` resource in DABs yet (on the roadmap per the Lakebase IaC owner,
anna.stepanyan, status through 2026‑06‑11). The `postgres` app resource binds to a **pre-existing**
project/branch/database — which we already require as a deploy prerequisite (DEPLOYMENT_GUIDE §3),
so this doesn't block us.

**⚠️ Stale-doc warning.** Our reference template, `agent-langgraph-advanced`, still has a README
line saying *"postgres resource not yet supported."* Eng confirmed (#lakebase-integration-agent-memory,
2026‑05/06) that line is **out of date** — the resource **is** supported; a fix is ticketed. Don't
trust that line.

**Confidence: High.** Multiple independent dated sources (CLI registry, DABs schema, the #help-dabs
resolution, public docs) agree.

### 3. The permission model is the hybrid we ship

Bind the resource for role + `CONNECT` + `CREATE`; then **`GRANT`** SELECT/USAGE on the operational
synced-tables schema from a seed-job task. A **branch superuser must run the GRANT** — the SP cannot
grant itself SELECT on a schema it doesn't own. This is exactly the pattern the internal
agent-platform team ships, and what `data/operational/05_grant_app_sp.py` does.

- Slack — **bryan.qiu (agents eng)**, #apps-devex, 2026‑04: the agent-platform team uses *"a skill +
  script to grant lakebase permissions to the SP"* (`app-templates/.claude/skills/lakebase-setup`,
  step 6 "Grant SP permissions").
- Docs — *Create Postgres roles* (2026‑05‑20): the documented manual role-create is the **SQL
  function** `SELECT databricks_create_role('<client-id>', 'SERVICE_PRINCIPAL');`, then *"you must
  grant the appropriate database privileges … on the specific databases, schemas, or tables."*
  `https://docs.databricks.com/aws/en/oltp/projects/postgres-roles`

> **There is no REST endpoint that grants Postgres privileges, and SP-role creation is a SQL
> surface.** The REST `/api/2.0/postgres/credentials` endpoint only **mints OAuth DB credentials**;
> grants are always SQL. (Our seed task's `POST /api/2.0/postgres/{branch}/roles` role-registration
> is therefore non-canonical — see "Recommended pattern" below.)

### 4. OBO vs SP

The SP is the recommended default for **app-owned shared state** (our checkpointer + pgvector
store). OBO (on-behalf-of-user) to Postgres is supported but **not turnkey-documented** — it needs a
per-user Postgres role and the Data API's authenticator role for RLS. Our split — **OBO for
user-facing data reads, SP for memory/checkpoint/operational state** — matches the guidance.

- Docs — *Configure authorization in a Databricks app*:
  `https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth`
- Docs — *Lakebase Data API* (per-user identity, RLS):
  `https://docs.databricks.com/aws/en/oltp/projects/data-api`

---

## How it's implemented

The model maps onto the repo like this:

| Object | Owner | App SP needs | Granted by |
|---|---|---|---|
| `public.*` synced read tables (`inventory_current`, `open_pos`, `suppliers`, `quality_incidents`, …) | platform `databricks_writer_*` | `USAGE` + `SELECT` | **seed task `grant_app_sp`** (superuser GRANT) |
| Agent-memory schema (LangGraph checkpoint + store) | App SP | `CREATE` + own | `postgres` app resource (`CONNECT` + `CREATE`) → SP self-creates at startup |
| Write-back schema `supply_chain_planner_app` (`approved_actions` / `planning_parameters` / `constraints`) | App SP | `CREATE` + own + DML | `postgres` app resource (`CONNECT` + `CREATE`) → SP self-creates at startup |
| MLflow experiment | deployer | `CAN_MANAGE` | `experiment` app resource |
| Vector Search / Genie / Foundation Models / UC reads | — | nothing (OBO) | runs as the signed-in user |

The concrete wiring:

- **[`databricks.yml`](../databricks.yml)** — the app's `resources:` block declares the `postgres`
  resource (`CAN_CONNECT_AND_CREATE`) alongside the `experiment`. The `database` field is built from
  `${var.lakebase_project}` / `${var.lakebase_branch}` / `${var.lakebase_database_resource}` (the
  last is the internal resource name — see the note below). The seed job's `grant_app_sp` task
  (`depends_on: sync_to_lakebase`) runs the SELECT grant; `app_name` is a single-source variable so
  the task can resolve the deployed App's SP.
- **[`data/operational/05_grant_app_sp.py`](../data/operational/05_grant_app_sp.py)** — slimmed to
  **SELECT-only**: resolve the App SP from `app_name`, then `GRANT USAGE` + `SELECT ON ALL TABLES`
  (and `ALTER DEFAULT PRIVILEGES … SELECT`) on `public`. It **no longer** hand-registers the
  Postgres role (the resource does that) and **no longer** runs `GRANT CREATE ON DATABASE` (the
  resource grants CREATE). Idempotent + best-effort-skip.
- **[`agent_server/config.py`](../agent_server/config.py)** — `lakebase_writeback_schema` (default
  `supply_chain_planner_app`) and `lakebase_memory_schema` name the two SP-owned schemas;
  `lakebase_operational_schema` (`public`) stays SELECT-only.
- **[`agent_server/operational_db.py`](../agent_server/operational_db.py)** — at startup
  `ensure_memory_schema()` and `ensure_writeback_tables()` `CREATE SCHEMA IF NOT EXISTS` + create
  the write-back tables in the SP-owned schemas. This is what relies on the resource's CREATE grant.
- **[`scripts/ensure_lakebase_project.py`](../scripts/ensure_lakebase_project.py)** — `make deploy`
  ensures the Lakebase project exists (idempotent) via `make lakebase-project` before deploying, so
  the `postgres` resource has a project/branch/database to bind to.

The role-registration fallback is now **rare** (the resource handles it on every deploy). If the
role ever hasn't propagated when the seed runs, the documented manual path is the **SQL**
`SELECT databricks_create_role('<client-id>', 'SERVICE_PRINCIPAL')` followed by the SELECT grants —
**never** the deprecated raw REST `POST …/roles` call. The legacy `scripts/grant_app_sp.sh` is
removed.

### Why write-back lives in `supply_chain_planner_app`, not `public`

The Meridian HITL commit target — `approved_actions`, `planning_parameters`, `constraints` — is
written **directly by the App SP**, not synced from Delta. Those tables can't live in `public`: the
synced operational schema is **owned by the platform's `databricks_writer_*` role**, and the
least-privilege SP has only SELECT there — it can't `CREATE TABLE` in `public`. The `postgres`
resource grants the SP `CREATE` **on the database**, which lets it create and own a **new** schema.
So write-back moved into the SP-owned `supply_chain_planner_app` schema (config
`lakebase_writeback_schema`), kept deliberately separate from both `public` (synced, SELECT-only)
and the LangGraph-owned memory schema. The SP `CREATE`s the schema and owns everything it writes —
no superuser grant on `public`, no `CREATE ON SCHEMA public` needed.

### Gotchas to keep in mind

- The SP client-id **changes when an app is deleted + recreated**, orphaning the old Postgres role.
  The resource re-registers the new SP on every redeploy, so this self-heals; the seed's
  `grant_app_sp` task re-resolves the SP each run, so its SELECT grant follows the new role too.
- The Postgres **role name == the SP client-id (a UUID)**, not a display name.
- The **deployer needs `CAN MANAGE` on the Lakebase project** to attach the `postgres` resource
  (add to the deployer-permissions checklist).
- Reports of **ownership flips on redeploy** when deploying app + Lakebase via an SP (eng triage;
  not intended behavior) — watch for it on shared/CI deploys.
- New schemas created by synced tables **don't inherit** the SP's grants, and
  `ALTER DEFAULT PRIVILEGES` doesn't cover *future* schemas — for an evolving schema set, the
  blessed automation is a `databricks_superuser`-owned `SECURITY DEFINER` function that re-GRANTs
  after each sync.

---

## Deploy-time requirements (recap)

- **Databricks CLI ≥ v0.294** (older versions treat the `postgres` resource key as an unknown
  field). See `docs/DEPLOYMENT_GUIDE.md` §2.
- The **deployer needs `CAN MANAGE` on the Lakebase project** to attach the `postgres` resource. See
  `docs/DEPLOYMENT_GUIDE.md` §6.A.
- The `postgres` resource binds to a **pre-existing** project/branch/database — `make deploy` ensures
  it via `make lakebase-project` (`scripts/ensure_lakebase_project.py`) before deploying.

---

## Citations

All dates are the source's last-updated stamp (public docs) or message timestamp (Slack),
verified mid-2026. Confidence noted where a claim leans on a single or older source.

**Public docs (High):**
- Add a Lakebase resource to a Databricks app — `https://docs.databricks.com/aws/en/dev-tools/databricks-apps/lakebase` (2026‑05‑22)
- Add resources to a Databricks app (resource types + privileges) — `https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources` (2026‑06‑01)
- DABs resources (`postgres` vs `database` keys) — `https://docs.databricks.com/aws/en/dev-tools/bundles/resources` (2026‑06‑09)
- Create Postgres roles (`databricks_create_role`, manual GRANTs) — `https://docs.databricks.com/aws/en/oltp/projects/postgres-roles` (2026‑05‑20)
- Connect external app to Lakebase using API (autoscaling; creds via `/postgres/credentials`) — `https://docs.databricks.com/aws/en/oltp/projects/external-apps-manual-api` (2026‑05‑15)
- Upgrade to Autoscaling (classic is legacy since 2026‑03‑12) — `https://docs.databricks.com/aws/en/oltp/upgrade-to-autoscaling`
- Configure authorization in a Databricks app (OBO) — `https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth`
- Lakebase Data API (per-user identity / RLS) — `https://docs.databricks.com/aws/en/oltp/projects/data-api`

**Slack (product/eng; High unless noted):**
- #help-dabs 2026‑03‑29 (tushar.madan) — autoscaling ⇒ `postgres` key, CLI ≥0.294, `database` field = internal resource name.
- #apa-apps 2025‑07‑07 (theo.fernandez, Apps eng) — "only CONNECT + CREATE." *(predates the autoscaling `postgres` split — cite for grant scope, not autoscaling support.)*
- #apa-lakebase 2026‑06‑02 (phil.sheffield, Lakebase) — "no Lakebase-specific auto-grant."
- #apps-devex 2026‑04 (bryan.qiu, agents eng) — agent-platform team's grant skill+script (matches our seed approach).
- #apa-lakebase, status through 2026‑06‑11 (anna.stepanyan, Lakebase IaC owner) — `postgres_databases`/`postgres_synced_tables` on the DABs roadmap, not shipped; Terraform ahead of DABs.
- #lakebase-integration-agent-memory 2026‑05/06 — `agent-langgraph-advanced` README "postgres resource not yet supported" is **stale** (fix ticketed).

See also [`references.md`](references.md) → *Databricks docs → Lakebase* for the broader Lakebase
doc set.
