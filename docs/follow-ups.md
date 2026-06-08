# Follow-ups / known gaps

Running list of deferred work surfaced while building the spine + operational + planner/HITL +
eval slices. Each item has enough context (root cause, files, fix direction) to pick up cold.

## 1. Planner over-escalates — `est_cost=None` always trips the gate  *(DONE — 2026-06-08)*

**Fixed.** Added an explicit `is_action_bearing` signal: the planner LLM now classifies whether a
recommendation commits spend / is risky-irreversible vs purely informational (and returns
`est_cost_usd=0` for informational answers). The gate is now
`needs_approval = is_action_bearing or (est_cost_usd is not None and est_cost_usd >= 50_000)`, so an
unknown cost no longer force-escalates informational answers. `is_action_bearing` is on
`_PlannerDraft` and `PlannerRecommendation` (visible in `custom_outputs` + the trace). Measured by
the new **deterministic `gate_correctness` scorer: 6/6 = 100%** (stable, no LLM); the LLM
`escalation_correctness` judge — which had swung 4/6–6/6 run-to-run — also reads 6/6 after the eval
record #1 rephrase. Files: `agent_server/graph/planner.py`, `agent_server/contracts.py`,
`agent_server/evaluate_agent.py` (`gate_correctness` + `is_action_bearing` + labels).

<details><summary>Original write-up</summary>

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

</details>

## 2. `custom_outputs` (approval card) not flowing through the streaming `/responses` path  *(DONE — 2026-06-08)*

**Fixed.** `stream_handler` now attaches `_custom_outputs(state, interrupt_payload)` to the final
`response.output_item.done` stream event (`ResponsesAgentStreamEvent` has a top-level
`custom_outputs` field), so the streaming `/responses` path carries the same structured payload
(route, recommendation, `approval_request`, status) as `/invocations`. Verified against the real
Lakebase checkpointer: the final event's `custom_outputs` carries `status=awaiting_approval` +
`approval_request`, and resume returns `status=completed` with the verdict. File:
`agent_server/agent.py`.

<details><summary>Original write-up</summary>

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

</details>

## 3. Checkpointer msgpack allowlist (forward-compat)  *(DONE — 2026-06-08)*

**Fixed.** `databricks_langchain.AsyncCheckpointSaver` does not forward a `serde` kwarg, so
`lakebase_context` now sets `checkpointer.serde` after construction to a
`JsonPlusSerializer(allowed_msgpack_modules=[…])` registering the 10 contract types
(`RouterDecision`, `KnowledgeResult`, `KnowledgePassage`, `GenieResult`, `OperationalResult`,
`OperationalRow`, `PlannerRecommendation`, `HITLDecision`, `HITLVerdict`, `DocType`). Verified: a
real Lakebase run + resume produces **zero** "Deserializing unregistered type" warnings (the resume
loads the checkpoint back through the serde). File: `agent_server/lakebase.py`
(`_CHECKPOINT_CONTRACT_TYPES`, `_contract_aware_serde`).

> Minor: MLflow's `MlflowLangchainTracer` lacks `on_interrupt`/`on_resume` callbacks, so autolog
> logs two benign warnings at the HITL boundary. Cosmetic; track if the interrupt span ever needs
> to be captured cleanly.

## Eval harness gaps (note for a more robust harness)

Surfaced while verifying #1. The current eval (`agent_server/evaluate_agent.py`) is good enough to
catch the over-escalation regression but is thin in places:

1. **Ambiguous escalation label.** ~~The record *"Show me similar past quality issues …"* is
   labeled approval=True but phrased as an informational retrieval.~~ **DONE (2026-06-08):**
   rephrased to ask for a recommendation (matching the canonical demo's "→ recommendation → gate"
   flow), and added an explicit `expected_action_bearing` label to every record.
2. **Stubbed gather masks routing/grounding quality.** The harness runs with `USE_STUBS=1`, so the
   stub gather returns fixed rows that don't match every question (e.g. "which suppliers are at
   risk?" → no risk data in the stub → `recommendation_grounded` and `routing_correctness` score
   "no" as **stub artifacts**, not real agent faults). Now that the operational + Genie data is
   provisioned, add a real-data eval mode (keep stubs only for fast CI).
3. **No deterministic gate scorer.** ~~`escalation_correctness` is an LLM judge (slow,
   nondeterministic and itself U2M-auth-limited).~~ **DONE (2026-06-08):** added `gate_correctness`
   — a deterministic (no-LLM) scorer comparing `is_action_bearing` to a per-record
   `expected_action_bearing` label, wired into both `evaluate_direct` and the `evaluate()` harness.
   Stable **6/6 = 100%**, vs the LLM `escalation_correctness` judge that swung 4/6–6/6 run-to-run.
   Also rephrased eval record #1 (the ambiguous bare "show me …") to request a recommendation,
   removing the borderline label (gap #1).
4. **No operational-SQL-correctness scorer.** The architecture doc calls for scoring the hybrid
   operational SQL/result; not implemented. With real data provisioned, assert the hero-scenario
   rows (Henkel/SKU-1001, on_hand 40 / open_po 500, scope filtering).
5. **Local trace round-trip limited (see #5).** `mlflow.genai.evaluate` (full harness) can't persist
   assessments under the local U2M profile; `evaluate_direct` works around it but doesn't store
   traces/assessments for review. Run the full harness on Databricks (ambient SP) or with a PAT.

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
