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
| Durable response schema `agent_server` (`responses` / `messages` — `databricks_ai_bridge` background mode) | App SP | `CREATE` + own + DML | `postgres` app resource → SP self-creates at startup **before** the library's `init_db` (see *The durable `agent_server` schema* below) |
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
  `ensure_memory_schema()`, `ensure_writeback_tables()`, and `ensure_durable_schema()`
  `CREATE SCHEMA IF NOT EXISTS` + create the tables in the SP-owned schemas. This is what relies on
  the resource's CREATE grant. `ensure_durable_schema()` runs **before** the durable `init_db()`
  (wired in `start_server._lifespan`, ahead of the library's lifespan) so the SP — not a developer's
  local run — owns the hard-coded `agent_server` schema; see *The durable `agent_server` schema*
  below.
- **[`scripts/ensure_lakebase_project.py`](../scripts/ensure_lakebase_project.py)** — `deploy.sh`
  phase 1 ensures the Lakebase project exists (idempotent) before deploying, so the `postgres`
  resource has a project/branch/database to bind to.

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

- The SP client-id **changes when an app is deleted + recreated** (it is **stable across deploys**,
  only destroyed on delete). The seed's `grant_app_sp` re-resolves the SP each run, so the *public*
  SELECT grant follows the new role. **But the SP-OWNED memory/write-back schemas do NOT self-heal**
  — the old SP still owns them and the new SP can't write them → the checkpointer falls through to
  `public` → `permission denied for schema public` crash. This is verified, not hypothetical (see
  "App recreate & the orphaned-schema lifecycle" below). It is the single biggest operational sharp
  edge in this design.
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

- **Databricks CLI ≥ v0.295** (`deploy.sh` enforces it in preflight; 0.294 first recognized the
  `postgres` resource key, older versions treat it as an unknown field). See `docs/DEPLOYMENT_GUIDE.md` §2.
- The **deployer needs `CAN MANAGE` on the Lakebase project** to attach the `postgres` resource. See
  `docs/DEPLOYMENT_GUIDE.md` §6.A.
- The `postgres` resource binds to a **pre-existing** project/branch/database — `deploy.sh` phase 1
  ensures it (`scripts/ensure_lakebase_project.py`) before deploying.

---

## App recreate & the orphaned-schema lifecycle (hardening)

> Researched 2026-06 (Glean + Slack + public docs) after a live deploy hit this exact crash. The
> agent-memory + write-back schemas are **owned by the app SP**, and that ownership is the sharp edge.

**The mechanism (verified live).** The Apps platform auto-mints the app SP; it is **stable across
deployments but destroyed on app delete**, and a **new SP (new client-id) is minted on recreate**
(docs: *Configure authorization in a Databricks app* — *"You can't change the service principal
assigned to an app or specify an existing service principal… The service principal remains the same
across all deployments… When you delete the app, Databricks deletes the service principal."*). The
Postgres **role name == the SP client-id**. So after a delete+recreate, the old SP's memory schema
is **orphaned**; `databricks_ai_bridge` does `CREATE SCHEMA IF NOT EXISTS` (a no-op on the orphan)
and `SET search_path TO <schema>, public`, the new SP isn't the owner, so the checkpointer's
`CREATE TABLE` falls through to `public` (no CREATE there, by design) → crash.

**You usually can't clean it up yourself.** `databricks_superuser` is **NOT a true Postgres
superuser** (`NOLOGIN`, no `rolsuper`, no `SET ROLE`/ADMIN OPTION) — it cannot `DROP` / `ALTER OWNER`
/ `REASSIGN OWNED` objects owned by a *different* (defunct) SP role. Tracked internally
(LKB-6733 / ES-1872947, open as of 2026-05, no ETA). Only the control-plane `cloud_admin` (Databricks
Support) can drop a stranded SP role. This was confirmed live: the deployer here is only an INHERIT
member of `databricks_superuser` and **could not** drop the orphaned schema.

### Prevention (in priority order)
1. **Redeploy in place; never delete the app.** The SP — and thus its Postgres role and owned
   schemas — is stable across deploys. Treat `bundle destroy`/app-delete as a deliberate, gated op.
2. **If you must recreate, remove the Lakebase resource first (as `CAN MANAGE`).** Removing the
   `postgres`/`database` app resource triggers the platform to **reassign the SP's owned objects to
   you and drop the SP role** — the clean lifecycle hook. Without `CAN MANAGE`, the resource detaches
   but objects are left orphaned.
3. **Pick a memory/write-back schema name the *current* SP can own** (no prior/defunct SP owns it).
   This is the interim fix applied here: `LAKEBASE_AGENT_MEMORY_SCHEMA` was pointed at a freed name
   (`supply_chain_planner_memory`) so the live SP creates + owns it. *Caveat:* this only relocates the
   future orphan — the next recreate orphans the new name.
4. **(Durable group-role ownership — STRUCTURALLY BLOCKED today, do not attempt.)** The tempting
   fix is a headless `NOLOGIN INHERIT` group role that owns the objects, with each app SP granted
   membership (a new SP just re-joins the group — no orphan). **It does not work on Lakebase as of
   2026-06** and was deliberately *not* implemented: (a) the checkpointer/store create their tables
   **as the connecting SP**, so for the group to own them you'd have to `SET ROLE <group>` before
   every DDL — and **`SET ROLE` is unsupported on Lakebase** (role flag `set=F`; confirmed
   apa-lakebase, Som Natarajan); (b) the Lakebase Data API (PostgREST) **does not support group-role
   auth** (JIRA LKB-9339). With no `SET ROLE`, neither the SP nor the (non-superuser) deployer can
   `ALTER … OWNER`/`REASSIGN` a foreign-owned schema either. So the robust strategy is **not** durable
   ownership but **prevention (1–2) + a loud preflight (below)** — which is what every FE demo
   (inventory-intelligence, ontobricks, devhub, the reference template) converges on. Revisit only if
   Lakebase ships `SET ROLE` / declarative role IaC.

### Recovery when already orphaned (non-superuser deployer)
- **Point the new SP at a fresh schema** and let it create+own it (cheapest; the orphan lingers,
  harmless). ← what we did.
- **If the old SP role still exists:** `GRANT <old_role> TO databricks_superuser;` then
  `REASSIGN OWNED BY <old_role> TO <temp_role>` + `ALTER SCHEMA … OWNER TO <new_sp>` (docs: *Transfer
  Postgres object ownership* — uses a temporary shared role; `REASSIGN OWNED` doesn't carry grants).
- **If the old SP role is already gone** (auto-dropped on app delete): no self-serve path — the
  objects are stranded; **file an ES/Support ticket** (only `cloud_admin` can drop them).

### Why the originally-planned "grant_app_sp reassigns ownership" hardening was dropped
It assumed the deployer is a Lakebase superuser. On a managed workspace the deployer is **not**:
`databricks_superuser` is `NOLOGIN` with `SET ROLE` disabled and **cannot `ALTER OWNER`/`DROP`/
`REASSIGN` objects owned by another role** (confirmed JIRA LKB-6733). So a seed-time `ALTER OWNER`/
`REASSIGN` of a foreign-owned schema fails. The realistic hardening is prevention (1–2 above) plus a
**loud preflight** — and that preflight is now **implemented**: `agent_server.operational_db`'s
`_ensure_role_owned_schema()` detects a memory / write-back / durable schema owned by a foreign
principal and **fails loud at startup with the exact GRANT/DROP remediation** (memory + write-back
are fatal; the durable `agent_server` schema degrades — background mode only), instead of letting the
app silently crash-loop on `permission denied`.

---

## The durable `agent_server` schema — a third orphan-class schema

> Hit live 2026-06: the deployed app logged `ERROR … [durable] stale-scan iteration failed …
> InsufficientPrivilege: permission denied for schema agent_server` on a loop, while otherwise
> working. Same *class* as the orphaned-schema problem above, on a schema this doc didn't cover and
> the orphan-fix couldn't reach.

**What it is.** `databricks_ai_bridge.long_running` (the `LongRunningAgentServer` powering our
run/poll/resume background mode) persists durable responses to its own Postgres tables —
`responses` + `messages` — in a schema whose name is **hard-coded** in the library:
`AGENT_DB_SCHEMA = "agent_server"` (`long_running/models.py`). At startup its `init_db()` runs
`CREATE SCHEMA IF NOT EXISTS agent_server` + `create_all`, and a background **stale-response
scanner** then `SELECT`s `agent_server.responses` every ~10s to fail orphaned background runs.

**Why it broke.** Unlike the memory schema, the name **can't be repointed** to a freed name — it's
hard-coded — so the orphan-fix trick (#3 above) doesn't apply. And whoever runs `CREATE SCHEMA …`
**first owns it**. A developer who ran the durable server **locally against the shared Lakebase
branch** created `agent_server` owned by their **own user** (our `.env` sets `LAKEBASE_AUTOSCALING_*`,
so the library's `is_db_configured()` is true locally). The deployed app SP's later
`CREATE … IF NOT EXISTS` then no-ops, the SP gets no `USAGE` → the scanner fails every iteration.
Non-fatal (caught + logged) but background mode is broken and the log fills with the traceback.
(The hard-coded name also means **any other `databricks_ai_bridge` durable app on the same Lakebase
database collides** on `agent_server`.)

**Reassigning ownership TO the SP is NOT self-serve.** `ALTER SCHEMA agent_server OWNER TO "<sp>"`
fails with `must be able to SET ROLE "<sp>" (SQLSTATE 42501)` — the owner isn't a member of the SP
role and can't add itself (the same "`databricks_superuser` is not a true superuser" limitation).
The two fixes an **owner** *can* run without `SET ROLE`:
- **(A, clean — what we did) `DROP SCHEMA agent_server CASCADE;` then restart the app.** The SP
  recreates+owns it on startup. Loses the (ephemeral) in-flight durable-response rows; needs a
  restart. Lands `agent_server` SP-owned, matching the memory/write-back schemas.
- **(B, stopgap) `GRANT`** `USAGE, CREATE` on the schema + table/sequence DML to the SP. Zero
  downtime, but human ownership remains → re-orphans if that role is ever dropped.

**Prevention (shipped + practice).**
1. **`ensure_durable_schema()`** (`operational_db.py`), called in `start_server._lifespan`
   **before** the library's durable `init_db()`: as the SP it creates+owns `agent_server` on a fresh
   branch (so the library's later create no-ops), and if it finds the schema **foreign-owned** it
   logs a **loud preflight** ERROR with the owner-runnable `GRANT` / `DROP`+restart remediation —
   instead of leaving the buried recurring scanner traceback. This is the durable structural fix for
   every future deploy.
2. **Don't run the durable server locally against the shared branch.** This is the root cause — a
   local run pollutes the shared `agent_server`. Use **branch-per-developer** (point local `.env` at
   a throwaway Lakebase branch), which the autoscaling model makes cheap, or otherwise avoid booting
   the durable server against the shared branch. A per-dev branch also isolates the memory/write-back
   schemas, sidestepping the orphan lifecycle above entirely.

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
