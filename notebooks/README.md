# notebooks/ — a guided tour of the Supply-Chain Planner Copilot

Five Databricks notebooks (`.py` files with the standard `# Databricks notebook source` /
`# COMMAND ----------` format) that walk a customer or field engineer through the architecture
interactively, calling the same production functions the deployed app uses — nothing here is a
reimplementation. Open them in the Databricks workspace (Repos / Git folder) and run top to bottom;
they also run locally as plain scripts via Databricks Connect (see each notebook's prereqs).

This is a companion to the deployed app's own live walkthrough (README.md's ["Try it"](../README.md#try-it)
section) — the notebooks trade the polished 2-minute demo for a slower, code-visible one.

## Run order

| # | Notebook | Shows | Prereqs | Real writes? |
|---|---|---|---|---|
| 0 | [`00_welcome.py`](00_welcome.py) | Persona framing, hero scenario, architecture diagram | none (self-guarded) | no |
| 1 | [`01_data_and_lakebase.py`](01_data_and_lakebase.py) | Delta → Lakebase Synced Tables → native pgvector → the hybrid SQL join | `make deploy` (operational chain) | no |
| 2 | [`02_genie_and_vector_search.py`](02_genie_and_vector_search.py) | Genie NL→SQL analytics + Vector Search retrieval, routing guidance | `make deploy` (Genie space + VS index) | no |
| 3 | [`03_agent_end_to_end.py`](03_agent_end_to_end.py) | Test requests, MLflow trace auto-render, HITL pause/approve/commit, short-term state | full `make deploy`; workspace notebook UI for trace rendering | yes (isolated schema) |
| 4 | [`04_long_term_memory.py`](04_long_term_memory.py) | Cross-session recall on a brand-new thread, raw memory table peek | notebook 03 run at least once | yes (isolated schema) |

**Only have 10 minutes?** Run `00 → 01 → 03 → 04`, skipping `02`.

## Before you start

- Run `make deploy PROFILE=<p>` at least once so the seed data, Genie space, and Vector Search
  index exist (see the repo root [`README.md`](../README.md)).
- **Each notebook is independently self-contained** — there's no `%run` chaining between them, so
  you can open any one on its own. Widgets at the top of each notebook configure it (catalog,
  schema, Genie space id, Lakebase project/branch); see "Databricks widgets" below.

## Databricks widgets — how these notebooks find your deployment

`agent_server/config.py` normally reads config from `.env` (local) or the deployed App's ambient
env vars — neither applies to a notebook you open by hand and attach to a cluster (Databricks
skips loading `.env`, and nothing wires the App's env vars into an ad hoc notebook session). So
these notebooks use **Databricks widgets** instead, via a pickup mechanism `agent_server/config.py`
already has (`_job_param_overrides()`, originally built for the `setup_and_seed` job): a widget
named after a `Settings` env alias (e.g. `UC_CATALOG`, `GENIE_SPACE_ID`) is picked up automatically
the first time `agent_server.config` is imported.

Two things to know:
1. **Change a widget, then use Run ▸ Clear State and Run All** (not just "Run All") — settings are
   resolved once per session and cached, so a widget change won't take effect on cells that already
   ran.
2. **Widgets are per-notebook.** Changing a value in one notebook doesn't carry over to another —
   set the same values wherever you open a different notebook in this tour.

Find your deployment's `GENIE_SPACE_ID` / `VECTOR_SEARCH_INDEX` / Lakebase project+branch in the
deployed App's Environment Variables tab (Databricks Apps UI) or via `databricks bundle summary`.

## What's real vs. isolated

Notebooks 00-02 are entirely read-only. Notebooks 03-04 run the real LangGraph agent against your
real Lakebase project, but write to an **isolated memory schema** (`notebook_tour_memory` by
default, widget-adjustable) — never the live app's `supply_chain_planner_memory` schema. Re-running
them is safe and additive (each run uses a fresh thread id).

See also: [`docs/architecture.md`](../docs/architecture.md), [`docs/storyboard.md`](../docs/storyboard.md).
