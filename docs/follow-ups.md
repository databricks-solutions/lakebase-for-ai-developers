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

## 4. Operational rows need Synced Tables provisioning  *(DONE — except the deploy-time App-SP grant)*

**Done (2026-06-08).** Provisioned the full Lakebase operational layer on the autoscaling
`production` branch and verified the hero scenario end-to-end on real data:
- Registered the Lakebase UC catalog `mfg_supply_chain_lakebase`
  (`databricks postgres create-catalog ... branch=projects/mfg-supply-chain-copilot/branches/production`)
  and set `LAKEBASE_UC_CATALOG` in `.env`.
- Seeded the native pgvector `quality_incidents` table (`02_pre_seed_pgvector.py`): 24 rows
  (23 active), 1024-dim embeddings, HNSW cosine index.
- Created the 6 Synced Tables (`03_sync_to_lakebase.py`) — all ONLINE. The two live tables
  (`inventory_current`, `open_pos`) follow `LAKEBASE_SYNC_MODE` (default **SNAPSHOT** — one-time
  copy, idle after, no always-on DLT pipeline cost; flip to **CONTINUOUS** + re-run `03` for a
  live-update demo). The 4 dims (`suppliers`, `product_dim`, `supplier_status`, `user_access`) are
  always SNAPSHOT. `03` reconciles mode changes by delete+recreate (no in-place policy update).
- `04_verify_hybrid_query.py` passes: in-scope `alex.miller` → hero Henkel `SUP-001`/`SKU-1001`,
  on_hand 40 / open_po 500, SUPERSEDED filtered; out-of-scope `planner.bob` → no adhesive rows.
- Graph runs end-to-end on the real operational path (real demo identity → 5 real rows + hybrid
  SQL in state; HITL interrupt fires; resume → commit).

**Fixed along the way.** `agent_server/tools/operational_tool.py` assumed tuple cursor rows, but
the app's `LakebasePool` cursor yields **mapping** rows — `dict(zip(cols, record))` mapped each
column name to a dict *key*. Now uses the record directly when it's a `Mapping`. `04` didn't catch
it because it connects via `data/operational/_lakebase.connect()` (plain tuple rows).

**Remaining (deploy-time only).** When the App is deployed (#6), grant its service principal read
access on the operational schema — printed by `03`:
`GRANT USAGE ON SCHEMA public TO "<app-sp-client-id>"; GRANT SELECT ON ALL TABLES IN SCHEMA public
TO "<app-sp-client-id>";` plus `ALTER DEFAULT PRIVILEGES`. Not needed locally (the connecting
user owns the objects).

**Note (smoke gotcha).** `agent_server/graph/_smoke.py` hardcodes `user_id="smoke@example.com"`,
which has no `user_access` scope → the real operational query returns 0 rows and the planner
confabulates. Use a real in-scope identity (`alex.miller@databricks.com` or `DEMO_PLANNER_USER`)
to exercise the real operational path; consider parameterizing the smoke's user id.

## 5. MLflow eval harness auth (local U2M)

`mlflow.genai.evaluate` can't persist assessments locally on the U2M OAuth profile (workspace
rejects the token type for trace/assessment artifact APIs). Use `evaluate_agent.evaluate_direct()`
locally; run the full harness (`evaluate(--harness)`) on Databricks (ambient SP) or with a PAT.

## 6. Cloud deploy

`databricks bundle deploy -t dev` (creates the App) deferred. `bundle validate` passes; resources
(app command/env, `postgres` + `experiment`) are filled in `databricks.yml`.
