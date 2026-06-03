# Sprint Plan — Supply-Chain Planner Demo (~1.5 weeks, team of 4–5)

**Goal:** a thin end-to-end slice that proves the architecture on the stack — not breadth. Build
with Claude Code against shared contracts so the workstreams don't block each other.

> Source: Google Doc → *Supply Chain Planner Agent Architecture → Sprint Plan* sub-tab. See
> [`architecture.md`](architecture.md) for the design and [`../CLAUDE.md`](../CLAUDE.md) for the
> P0/P1/P2 scope tiers and conventions.

## Definition of done (the slice we demo)

A user submits the Acme-style request in the **Databricks App** → the **supervisor** routes it →
the **pgvector (Operational)** and **Genie (Analytics)** agents return real results → the
**planner** produces a recommendation that trips the **gate** → an **HITL approval card** appears
→ the user approves → the decision **commits** and the result is shown. The whole run is visible
as a single **MLflow trace**, and the run is **resumable from the Lakebase checkpoint** across
the HITL pause. If that works on seeded data, the demo is done.

## Must-have vs nice-to-have

| Capability | Tier | Note |
|---|---|---|
| Supervisor + routing | **Must** | orchestration core |
| Operational agent — pgvector hybrid (similarity + join to on-hand/POs) | **Must** | the differentiator + project thesis; riskiest — front-load it |
| Analytics agent — Genie | **Must** | real lakehouse data, low effort (managed) |
| Planner (single, **sequential**) | **Must** | produces recommendation + `needs_approval` / `est_cost` |
| Lakebase **short-term** checkpointer | **Must** | durable, resumable runs; HITL depends on it |
| HITL approve / reject via `interrupt()` | **Must** | headline capability for the technical persona |
| MLflow tracing (autolog) | **Must** | high value, low cost — the "look inside" |
| Databricks App (run / poll / resume) | **Must** | demo surface; two-call resume around the pause |
| Seeded demo dataset + Acme scenario | **Must** | reproducible story |
| On-behalf-of-user (OBO) auth | **Must** | big selling point for agents on Databricks |
| **DABs deployment** (App + setup/seed job + UC objects + experiment; `dev`/`demo`) | **Must** | one-command reproducible env; Day-2 skeleton deploys via the bundle. Genie space stays manual |
| Long-term Lakebase store (cross-session memory) | **Must** | showcase capability; dig deeper into specifics |
| Knowledge agent (Vector Search) + pgvector↔VS side-by-side | **Nice → promote** | completes the comparison thesis; promote if pgvector opinion is the headline |
| Parallel gather + planner fan-out (`Send`) | Nice | sequential is easier to debug for v1 |
| HITL **edit + replan** | Nice | approve/reject is enough for v1 |
| MLflow **evaluation** (judges, eval set) | Nice | tracing suffices for the demo |

## Workstreams (one owner each)

- **WS1 — Spine, App & DABs (lead/integrator) — Alex & Kylie.** Graph skeleton, supervisor
  routing, Lakebase checkpointer **and store** wiring (`PostgresStore` into `compile()` + prefs
  hydrate-read at run start), MLflow autolog, App run/poll/resume handlers, the **DABs bundle**
  (`databricks.yml`, App resource, setup/seed job, `dev`/`demo`). Owns the shared state schema,
  agent I/O contracts, and `CLAUDE.md`. Unblocks everyone.
- **WS2 — Operational data (strongest data eng) — Chandhana.** Synthetic operational tables +
  Synced Tables into Lakebase; the hybrid SQL (vector similarity + access predicate + join to
  on-hand/open POs) wrapped as the agent tool. Memory vectors/index are LangGraph-managed via
  `AsyncDatabricksStore` (embeddings via the endpoint, not `ai_query`). **Spikes the hybrid query first.**
- **WS3 — Genie (+ Vector Search if promoted) — Ram / Kylie.** Genie space + Conversation API
  wrapper for the Analytics agent; VS index + Knowledge agent as the stretch.
- **WS4 — Planner + HITL + demo.** Planner node + gate/threshold, the `interrupt()` approval
  card, seeded demo data, demo narrative / run-of-show.
- **WS5 — LangGraph, Routing — Ram.**

## Phased timeline (~8 working days)

- **Phase 0 — Foundation (Day 1).** All-hands AM to lock the state schema, Pydantic I/O
  contracts, repo scaffolding, `CLAUDE.md`. Scaffold the DABs bundle (`databricks.yml`, App
  resource, `dev` target, empty setup-job). Provision Lakebase, Genie, (Vector Search). WS2
  starts the pgvector spike immediately. *Exit:* stubs compile; everyone develops against contracts.
- **Phase 1 — Walking skeleton (Day 2).** Graph runs end-to-end with **stubbed** nodes: routes,
  checkpoints to Lakebase, emits an MLflow trace, returns a canned result — and **deploys to
  Apps via `databricks bundle deploy`** to each dev's `dev` target. Kills integration AND
  deployment risk early. Review the pgvector spike; re-check scope.
- **Phase 2 — Component build against contracts (Days 3–4).** Each WS replaces its stub with the
  real node behind the contract. Mid-Day-4, swap one or two real nodes onto the spine to flush
  interface mismatches.
- **Phase 3 — Integration, HITL & memory (Days 5–6).** All real nodes on the spine. Real HITL
  approve/reject with checkpoint pause/resume. Wire the long-term store (hydrate prefs at start,
  write verdict at commit, validate the "smarter second run"). Stand up the shared `demo` target
  + seed; run Acme end-to-end.
- **Phase 4 — Polish & dry-run (Days 7–8).** Trace + UI cleanup so the run reads as a narrative;
  rehearse on `demo`; freeze scope. A clean `bundle deploy + seed` is the backup recovery path.
  Half-day buffer + a backup recording.

## Risk triggers (decide fast)

- **pgvector spike slow/fails by Day 2** → fall back to a single-query join with app-side access
  scoping (keep the join story, drop in-query for v1), or descope Vector Search to protect it.
- **Apps deployment friction** (auth, resource limits, package install) → caught by deploying
  the skeleton through the bundle on Day 2, not demo-eve.
- **DABs resource gaps** → Genie space isn't a clean DABs resource (create manually, reference
  as an app resource); don't burn time bundling non-resources.
- **Genie Conversation API quirks** (async, permissions) → wire it Day 2–3, not late.
- **Long-term state is additive scope** → likely puts Vector Search out for v1; if pgvector
  slips at the Day-2 trigger, cut memory to a write-back-only stub (persist verdict, skip
  hydrate-and-use) so it never threatens the core loop.
- **Scope creep into parallelism / long-term memory** → explicitly parked; revisit only if Day 6
  is green.

## Claude Code working agreement

- One `CLAUDE.md` at the repo root carrying architecture, I/O contracts, conventions, and
  explicit do/don'ts — so every engineer's sessions share context and don't drift. Skills are
  scaffolded under `.claude/skills/` alongside references.
- A stub/mock per agent so each workstream builds and tests in isolation against the contract.
- Small, contract-respecting PRs. Changing a contract is a team decision, not solo.
