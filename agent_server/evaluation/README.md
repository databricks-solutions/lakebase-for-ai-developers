# Evaluation flywheel

A tight, repeatable dev feedback loop. Two suites, one entry point (`agent-evaluate` → `cli.main`):

- **Quality flywheel** — runs the agent over a labelled sanity set and scores each question on
  **output quality** (LLM judges + a deterministic gate) **and trace shape** (span structure,
  per-node latency, token budget), diffs a committed baseline, writes `eval_report.md`.
- **Memory/state validation** (`--memory`) — the deep Lakebase test the quality flywheel can't do:
  multi-turn against a **real Lakebase store + checkpointer** (isolated schema), asserting write
  policy, cross-session recall, graph order, and short-term threading. See **Memory suite** below.

Drive the quality flywheel (and auto-analyze failing traces) with the `/flywheel` slash command.

## Run

```bash
# Flywheel (graph = in-process; server = live local /invocations with real span tree)
uv run agent-evaluate --layer graph  --full           # judges + gate + trace scorers
uv run agent-evaluate --layer server --full           # needs `uv run start-server` up
uv run agent-evaluate --layer graph  --fast --limit 2 # quick smoke: deterministic+trace scorers only
uv run agent-evaluate --layer graph  --full --update-baseline   # set the baseline

# Legacy paths (preserved)
uv run agent-evaluate            # evaluate_direct (trace-free, prints pass rates)
uv run agent-evaluate --harness  # mlflow.genai.evaluate (persists assessments)
```

Outputs `eval_report.md` + `eval_results.json` in the cwd (both git-ignored; regenerated each run).

## Two independent axes

- **`--fast` / `--full`** — whether the LLM judges run. `fast` = deterministic gate + trace scorers
  only (a sub-minute smoke that still calls the router/planner LLM); `full` (default) adds the three
  judges (`routing_correctness`, `recommendation_grounded`, `escalation_correctness`).
- **gather data** — stub by **default** (deterministic, repeatable scoring against the demo hero
  rows). Pass `--real-data` to flip `USE_STUBS` off and exercise the live operational hybrid query /
  Genie / Vector Search — it works (operational rows are live; the `--memory` suite surfaces real
  suppliers) but is non-deterministic and slower.

## Baseline (warn-only)

`baseline_metrics.json` (committed, here in the package) is the regression reference. Each run diffs
quality pass rates, end-to-end p95 latency, and avg tokens against it and flags regressions in the
report — but the run never fails (no exit gate yet). Latency is layer-dependent, so regenerate the
baseline per layer with `--update-baseline`. Tune latency budgets via `FLYWHEEL_LATENCY_BUDGETS_MS`
(JSON) and the token budget via `FLYWHEEL_TOKEN_BUDGET`.

## Memory suite (`--memory`)

```bash
uv run agent-evaluate --memory                 # all scenarios + trace report + assertions
uv run agent-evaluate --memory --drop          # ...then print the drop-schema SQL
uv run agent-evaluate --memory --no-clean      # keep prior memory (don't drop the schema first)
uv run agent-evaluate --memory --memory-schema scp_mem_validation --validation-user me@db.com
uv run python -m agent_server.validate_memory  # back-compat shim → same entry
```

Stands up a **real** Lakebase store + checkpointer in a THROWAWAY schema (default
`scp_mem_validation`) and drives multi-turn scenarios: approve→write-all-three, new-thread recall,
reject→audit-only, informational→audit-only, same-thread short-term. Reads operational rows
read-only from the real schema (so it forces `USE_STUBS=0`); production memory is never touched.
Writes `memory_report.md` + `memory_results.json` (assertions, per-scenario spans/latency/tokens).
Needs Lakebase access — it does NOT need `start-server`. Drop the schema after with
`DROP SCHEMA IF EXISTS scp_mem_validation CASCADE`.

## Known gaps

- **No operational-SQL-correctness scorer.** `scorers.py` covers `routing_correctness`,
  `recommendation_grounded`, `escalation_correctness`, and the deterministic `gate_correctness` —
  but not the hybrid operational SQL/result the architecture doc calls for scoring. With
  `--real-data` provisioned, add a scorer that asserts the hero-scenario rows (Henkel `SUP-001` /
  `SKU-1001`, on-hand 40 / open-PO 500, and out-of-scope access filtering). (Migrated from the
  former `docs/follow-ups.md`; everything else in that list shipped.)

## Modules

`dataset.py` (EVAL_RECORDS) · `scorers.py` (judges, gate, trace predicates + `TraceSummary`) ·
`runners.py` (graph/server run paths + trace fetch/summarize) · `baseline.py` · `report.py` ·
`cli.py` (dispatch + `run_flywheel`) · `memory_validation.py` (the `--memory` suite).
`agent_server/evaluate_agent.py` and `agent_server/validate_memory.py` are back-compat shims.
