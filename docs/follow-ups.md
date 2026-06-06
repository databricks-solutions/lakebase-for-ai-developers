# Follow-ups / known gaps

Running list of deferred work surfaced while building the spine + operational + planner/HITL +
eval slices. Each item has enough context (root cause, files, fix direction) to pick up cold.

## 1. Planner over-escalates — `est_cost=None` always trips the gate  *(eval finding)*

**Symptom.** The HITL gate fires on essentially every run, including purely informational
questions ("total open POs by supplier for Q4", "which suppliers are at risk?"). The
`escalation_correctness` scorer scored **4/6 (67%)** because of this; routing and grounding were
5/6 each.

**Root cause.** `agent_server/graph/planner.py` — the planner LLM (`_PlannerDraft`) often returns
`est_cost_usd = None`, and the gate is `needs_approval = est_cost is None or est_cost >= 50_000`
(`APPROVAL_COST_THRESHOLD_USD`). Null cost is treated as "needs approval" (safe default), so the
gate trips for informational answers that shouldn't require sign-off. This was also visible in the
MLflow trace (`hitl_review` reached on every SUBMIT).

**Fix direction (pick one or combine).**
- Prompt the planner harder to always return a numeric `est_cost_usd` (or 0 for informational
  answers), so the threshold is meaningful.
- Derive cost deterministically from the proposed actions/quantities (reorder/expedite/re-source
  units × unit cost) instead of trusting the LLM.
- Add an explicit "is this action-bearing vs informational?" signal and only gate action-bearing
  recommendations.

**How to re-measure.** `uv run python -m agent_server.evaluate_agent` (the `evaluate_direct` path)
prints per-question verdicts + pass rates; watch `escalation_correctness`.

## 2. `custom_outputs` (approval card) not flowing through the streaming `/responses` path

**Symptom.** `POST /invocations` (sync) returns full structured `custom_outputs` (route,
recommendation, `approval_request`, trace_notes, status). The background **`/responses`**
(streaming) path returns only the assistant text — the structured approval-card payload is lost.

**Root cause.** `agent_server/agent.py` — `invoke_handler` builds `custom_outputs` via
`_custom_outputs(...)`, but `stream_handler` only yields the final text `output_item.done`. The
streaming path needs to surface the same structured payload (and the interrupt/`approval_request`)
so the UI can render the HITL approval card from a streamed run.

**Fix direction.** Emit the structured state on the stream — either a final
`response.completed`/custom event carrying `custom_outputs`, or a dedicated approval-card event
when `interrupt_payload` is set. Wire this when the Next.js frontend lands (Slice 4) since that's
the consumer.

## 3. Checkpointer msgpack allowlist (forward-compat)

LangGraph warns it will eventually block deserializing the contract Pydantic types from the
checkpoint (`RouterDecision`, `PlannerRecommendation`, `HITLDecision`, gather results). Resume
works today (verified across a server restart). Before a future LangGraph upgrade, register these
modules in the checkpointer's serde `allowed_msgpack_modules` (requires passing a custom serde into
the `databricks_langchain` `AsyncCheckpointSaver`).

## 4. Operational rows need Synced Tables provisioning

`agent_server/tools/operational_tool.py` runs the validated hybrid SQL, but the 6 Synced Tables
(`inventory_current`, `open_pos`, `user_access`, …) aren't provisioned on the autoscaling
`production` branch. Register a Lakebase UC catalog (`databricks postgres create-catalog`), set
`LAKEBASE_UC_CATALOG`, run `data/operational/03_sync_to_lakebase.py`, grant SELECT, then verify the
hero-scenario assertions. Unblocks the full Acme demo (Slice 4) and the operational-SQL scorer.

## 5. MLflow eval harness auth (local U2M)

`mlflow.genai.evaluate` can't persist assessments locally on the U2M OAuth profile (workspace
rejects the token type for trace/assessment artifact APIs). Use `evaluate_agent.evaluate_direct()`
locally; run the full harness (`evaluate(--harness)`) on Databricks (ambient SP) or with a PAT.

## 6. Cloud deploy

`databricks bundle deploy -t dev` (creates the App) deferred. `bundle validate` passes; resources
(app command/env, `postgres` + `experiment`) are filled in `databricks.yml`.
