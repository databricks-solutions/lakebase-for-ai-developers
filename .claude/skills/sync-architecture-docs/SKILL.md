---
name: sync-architecture-docs
description: "Keep this repo's architecture diagrams and prose docs in sync with the code as it evolves. Use this whenever the user asks to update/refresh the architecture diagrams, sync docs with the code, audit docs for staleness, or check that docs/architecture.md still matches the implementation — and proactively after any significant change (a new graph node, tool, Lakebase schema, data-pipeline step, auth change, or frontend swap), since the diagrams and READMEs drift silently. Covers refreshing the four Mermaid diagrams in docs/architecture.md, reconciling the known stale-doc patterns, and verifying with greps + Mermaid validity."
---

# Sync architecture docs

Docs and diagrams drift the moment the code moves and nobody notices until a demo or a new
teammate hits a wrong fact. This skill is the **maintenance loop** that re-aligns the repo's
architecture docs with the actual implementation. It is *not* for first-time authoring — the
diagrams and canonical docs already exist; you are keeping them true.

**Canonical home for diagrams:** [`docs/architecture.md`](../../../docs/architecture.md), under the
`## End-to-end architecture` heading — four layered Mermaid views. Everything else (READMEs,
`CLAUDE.md`) links to it; don't scatter duplicate diagrams elsewhere.

## When this applies

Run the loop after work that changes any of: the LangGraph topology (nodes/edges/parallelism),
a gather tool, a Lakebase schema or write-back table, the seed/data pipeline, the auth model
(OBO vs app-SP, `user_api_scopes`), the frontend stack, or the LLM endpoints. Also run it on
demand when the user asks to "refresh the diagrams" or "audit the docs."

## The loop

### 1. Explore the changed surfaces

Read the source of truth — never describe from memory, names change. If scope is uncertain,
fan out parallel `Explore` agents over these surfaces:

| Surface | Source of truth |
|---|---|
| Graph topology (nodes, edges, parallelism, `interrupt()`) | `agent_server/graph/build_graph.py` |
| Gather tools + what each connects to | `agent_server/tools/{operational_tool,genie_tool,knowledge_tool}.py` |
| Schemas, endpoints, LLM routes, env vars | `agent_server/config.py` |
| App resources, OBO `user_api_scopes`, seed job tasks | `databricks.yml` |
| Seed/data pipeline (UC → Lakebase / pgvector / VS / Genie) | `data/**` (esp. `operational/03_sync_to_lakebase.py`, `05_grant_app_sp.py`, `knowledge/03_build_vs_index.py`) |
| Frontend stack + API calls | `frontend/package.json`, `frontend/src/**` |

### 2. Refresh the four Mermaid diagrams in `docs/architecture.md`

Re-check every node/table/index/endpoint/scope name against step 1, and update where the code
moved. The four views and what each must keep accurate:

1. **End-to-end overview** — seed job → UC / Lakebase / Genie / VS → LangGraph App → Vite SPA.
2. **Data / seed pipeline** — the `setup_and_seed` job's chains with real task + table/index names.
3. **Agent runtime graph** — real node names; each gather node annotated with backend + auth;
   `interrupt()` marked on the HITL node; planner marked sequential if `Send` fan-out is still P2.
4. **Permissions / UC + OBO map** — the App-SP grants vs the signed-in-user OBO scopes.

Mermaid hygiene (so it renders on GitHub): use ```mermaid fences and `flowchart` (not the
deprecated `graph`); avoid the reserved id `graph`; quote labels containing `()` `:` `<>` or use
`<br/>`; keep subgraphs balanced.

### 3. Reconcile the known stale-doc patterns

These recur — check each across `CLAUDE.md`, `README.md`, `agent_server/README.md`, `data/README.md`,
and `docs/**`. Each is "what the docs tend to say" → "what's actually true; verify, don't assume":

- **Frontend stack** — must read **Vite + React + TypeScript** (served at `/ui`), *not* Next.js.
  Next.js is only correct when describing the *upstream* `agent-langgraph-advanced` template.
- **Demo narrative vs seeded data** — the canonical seeded hero is **Henkel (SUP-001) / SKU-1001
  adhesive quality** (on-hand 40, open POs ~800, Q4 2026). Pitch narratives (e.g. an IGBT
  shortage) are illustrative only and must say so, not masquerade as the seeded scenario.
- **Placeholder/aspirational text** — phrases like "no agent code yet" or "WS1 will bring this in"
  are stale; the code is implemented.
- **Planner shape** — sequential today; per-SKU/supplier `Send` fan-out is P2. Don't claim a
  shared `plans` reducer key — it produces a `recommendation` + per-action `PlannedAction`s.
- **Auth split** — Knowledge (Vector Search) and Analytics (Genie) run **OBO** (user token);
  Operational (Lakebase hybrid SQL) runs as the **App service principal**.
- **Lakebase schemas** — three: `public` (operational synced tables + pgvector `quality_incidents`),
  `supply_chain_planner_memory` (checkpointer + store, SP-owned), `supply_chain_planner_app`
  (Meridian write-back, SP-owned).

When you confirm a fact that *isn't* in this list but keeps drifting, add it here so the next run
catches it cheaply.

### 4. Guardrail — don't touch vendored skills

`.claude/skills/` is mostly **vendored + SHA-pinned** upstream copies (see
[`UPSTREAM.md`](../UPSTREAM.md)). Editing a vendored `SKILL.md` body creates phantom drift and
breaks the refresh diff. Fix only repo-**authored** surfaces: the index in
[`README.md`](../README.md), this skill, and any other skill explicitly marked "repo-authored,
not vendored." If a vendored skill states something now-wrong about this repo (e.g. a run command),
correct it in the repo-authored index/README, not in the vendored body.

### 5. Verify

- **Stale markers gone** — grep the tracked docs (exclude `.claude/skills/` vendored bodies):
  `grep -rn "Next.js\|e2e-chatbot\|No agent code yet\|Acme" --include='*.md' .` should return only
  intentional hits (upstream-template descriptions, historical "replaces the old placeholder"
  notes). Investigate anything else.
- **Mermaid valid** — GitHub renders ```mermaid natively; locally use the `bierner.markdown-mermaid`
  VS Code/Cursor extension, or `mmdc` (`@mermaid-js/mermaid-cli`) to render each block and catch
  syntax errors.
- **No broken links** — if a doc was moved/deleted, grep for stale relative links to it.

## Output discipline

Make small, factual edits — this is reconciliation, not a rewrite. Group the changes into one
focused docs commit (e.g. `docs: sync architecture diagrams + accuracy cleanup`) and summarize per
file. Don't commit unless the user asks.
