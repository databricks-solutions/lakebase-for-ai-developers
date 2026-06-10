---
description: Run the agent-evaluation flywheel (quality + trace-shape scorers + baseline diff), then root-cause failing traces.
argument-hint: "[--layer graph|server] [--fast|--full] [--limit N] [--update-baseline] [--real-data]"
allowed-tools: Bash, Read, Skill, Edit
---

You are running the **agent-evaluation flywheel** for this repo. It runs the eval harness
(`agent_server/evaluation`), which scores each sanity question on output quality (LLM judges +
deterministic gate) **and** trace shape (span structure, per-node latency, token budget), diffs a
committed baseline, and writes `eval_report.md` + `eval_results.json`. Your job is to drive it and
then root-cause any failures with the `analyze-mlflow-trace` skill.

Arguments from the user: `$ARGUMENTS` (may be empty). Defaults: `--layer server`, `--full`.

Do the following, narrating briefly:

1. **Parse args.** Determine `layer` (graph|server, default server), `mode` (`--fast`→ deterministic
   + trace scorers only, no LLM judges; default `--full`→ adds the LLM judges), whether `--real-data`
   was passed (default: stub gather data — deterministic, repeatable scoring against the demo hero
   rows; `--real-data` exercises the live operational query but is non-deterministic), and pass
   through `--limit`,
   `--update-baseline`, `--base-url`. If anything is ambiguous, state your assumption and proceed.

2. **Server layer only — ensure the server is up.** Check `curl -s -o /dev/null -w "%{http_code}"
   http://localhost:8000/health` (or the `--base-url`). If it's not reachable, start it in the
   background per the `run-locally` skill: unless `--real-data` was passed, export `USE_STUBS=1`
   first (the *server* owns its env, so this is how the run uses stub gather data), then
   `uv run start-server` (run_in_background). Poll `/health` until it returns 200, up to ~60s. Note:
   the server's lifespan opens Lakebase — if that fails locally (no Lakebase access), report it and
   fall back to suggesting `--layer graph`, which needs no server. Don't keep retrying.

3. **Run the harness.** `uv run agent-evaluate <parsed flags>` (e.g.
   `uv run agent-evaluate --layer server --full`). It needs Databricks auth (the local CLI profile
   from `.env`); the harness pins an OAuth token itself. This can take a few minutes in `--full`.

4. **Read results.** Read `eval_results.json` (machine-readable). Note the `metrics.scores`, any
   `baseline_diff` entries with `"regressed": true`, and `failing_trace_ids`.

5. **Root-cause failures.** For each id in `failing_trace_ids` (cap at ~3 to stay focused), invoke
   the `analyze-mlflow-trace` skill with that trace id to get a root-cause summary. Briefly fold each
   finding back into the matching `### Q:` block in `eval_report.md` under a `**trace analysis:**`
   line (use Edit). If there are no failing traces, skip this step.

6. **Summarize to the user.** Report: per-scorer pass rates, any regressions vs baseline (these are
   warn-only — the run does not fail), the slowest spans, and the top 1-2 root causes for failures.
   Point them at `eval_report.md` for the full table.

Stay focused on this loop. If the harness or server fails after a couple of attempts, stop and report
what went wrong rather than thrashing.

**Sibling suite — memory/state validation.** If the user asks to validate memory/Lakebase state
(short-term checkpoint + long-term store) rather than output quality, run
`uv run agent-evaluate --memory` instead. It stands up a real Lakebase store in a throwaway schema
and asserts write policy / cross-session recall / graph order / short-term threading, writing
`memory_report.md`. It needs Lakebase access but NOT the server. Summarize the assertion pass count
and any FAILs, and remind the user to drop the schema (`DROP SCHEMA IF EXISTS scp_mem_validation
CASCADE`) when done.
