# Phase 2 workstreams — agent memory & Lakebase (team alignment)

> **Purpose.** Phase 1 (long-term memory read/write + dev/prod Lakebase branching) is **built and
> committed**. Phase 2 adds advanced short-term memory and the per-session sandbox. These touch
> **shared infra** — branch lifecycle, scheduled jobs, the request hot-path, and SDK branch CRUD —
> so this doc exists to align the team **before** we build WS0/WS2/WS3/WS4. WS1 is the only piece
> slated for immediate implementation.
>
> **Owner:** Alex · **Status date:** 2026-06-09 · Source of truth for the detailed design.

## Status at a glance

| WS | What | Status |
|----|------|--------|
| **Phase 1** | Long-term memory (curated write + hydrate/read), dev/prod **branching**, grant script | ✅ **Done** (commit `efd8e73`, branch `ws/long-term-memory-and-branching`) |
| **WS1** | Multi-turn short-term memory (messages + trim/summarize) | 🔜 **Build next** |
| **WS0** | Branching helper module + integration-test harness (foundation) | 📋 Documented — needs team OK |
| **WS2** | Checkpoint prune (script + scheduled DABs job) | 📋 Documented — needs team OK |
| **WS3** | Per-session sandbox branch (opt-in) + merge-back + demo | 📋 Documented — needs team OK |
| **WS4** | Integration tests on a branch-per-test harness | 📋 Documented — needs team OK |

## What Phase 1 already established (context for the rest)

- **Long-term memory is now bidirectional.** `agent_server/memory.py` is the single source of truth
  for namespaces (`approvals` / `preferences` / `supplier_notes`), the **curated write policy**
  (`build_memory_writes`: audit every decision; learn preferences + supplier-notes only on approved
  action-bearing outcomes), and scoped semantic recall. A `hydrate_memory` graph node
  (gather fan-in → hydrate → planner) injects recalled memory into the planner; `commit_node`
  writes it. Embedding is on a curated `memory_text` field (`index=["memory_text"]`), not the whole
  JSON.
- **Dev/prod isolation is by Lakebase BRANCH** (idiomatic for a single workspace). Same schema name
  everywhere; `databricks.yml` sets the branch per target (`dev → development`, `demo → production`),
  local `.env → development`. Separate *projects* are reserved for cross-workspace / multi-tenant.
- **Known open item (not Phase 2):** `production`'s `supply_chain_planner_memory` is still
  developer-owned (the App SP fell back to an orphan `…_memory_app`). The fix (drop+redeploy so the
  SP owns it, or grant the SP access) is **destructive on prod** and needs a go-ahead. See
  `data/operational/README.md` → "Agent memory: dev vs prod".

## Decisions already made (with the requester)

1. **Dev/prod isolation = branching** within the one workspace (not schemas, not projects).
2. **Per-session sandbox = opt-in app mode + a demo driver** (never the default request path).
3. **Short-term compaction = trim + LLM summarization** (not trim-only).
4. **Checkpoint prune = script + scheduled DABs job** (not script-only).

## Confirmed feasibility (installed versions — no surprises)

- **Branch CRUD from Python:** `w.postgres.create_branch / delete_branch / get_branch /
  list_branches` (databricks-sdk 0.114.0); `BranchSpec` supports `source_branch`, `ttl`, `no_expiry`.
- **Per-branch resources:** `AsyncDatabricksStore` / `AsyncCheckpointSaver` accept `project`/`branch`
  — but opening a pool per session is **expensive** (token + DNS), and the app uses a **singleton**
  opened once at startup. ⇒ per-session branches must be **opt-in**.
- **Checkpoint prune:** `adelete_thread(thread_id)` exists; **no native TTL** — prune is an age-based
  job over the `checkpoint->>'ts'` JSONB field (no `created_at` column).
- **Multi-turn:** `add_messages` + `MessagesState` + `trim_messages` available (langgraph 1.2.2 /
  langchain-core 1.4.0). **No** `langmem`, **no** prebuilt SummarizationNode, **no** `pre_model_hook`
  ⇒ summarization is a small custom node.

---

## WS0 — Foundation: branching helpers + integration-test harness

*Unblocks WS3 and WS4; do first.*

- **New `agent_server/branching.py`** — the shared primitive:
  - `fork_branch(project, source_branch, branch_id, *, ttl_seconds=None, no_expiry=False) -> str`
    (via `w.postgres.create_branch`); idempotent `ensure_branch(...)`; `delete_branch(name)`.
  - `branch_config(base, branch_id)` — clone the `LakebaseConfig` onto another branch (forked
    branches auto-provision a `primary` endpoint, as `development` did).
  - `@asynccontextmanager session_branch_resources(base, branch_id)` — open a checkpointer+store on
    that branch, **reusing `lakebase.lakebase_context` construction + `_contract_aware_serde`**.
- **New `tests/conftest.py`** — `integration_branch` fixture gated on `RUN_LAKEBASE_INTEGRATION=1`
  (else skip): fork `it-<uuid>` (TTL ~2h) off the configured branch, yield its config/resources,
  **delete on teardown**. Register an `integration` marker so default `pytest` runs `-m "not
  integration"` (offline, fast).

**Open questions for team:** (a) branch-naming convention (`it-*`, `session-*`, `dev-<name>`)?
(b) who/what cleans up leaked branches if a test crashes — rely on TTL + the WS2 sweep? (c) should
the integration suite run in CI (needs workspace creds) or stay local/nightly?

## WS1 — Multi-turn short-term memory  *(build next)*

The problem: `AgentState` has no `messages` key; each turn overwrites `question`, so the planner
can't follow "make it cheaper" / "what about supplier X" across turns, and nothing bounds a long
thread.

- **`state.py`** — add `messages: Annotated[list[AnyMessage], add_messages]` + `running_summary: str`.
  (LangChain messages + `RemoveMessage` are natively serde-safe; no new contract types.)
- **`agent.py`** — send only the **new** turn each request (`messages=[HumanMessage(latest)]`); the
  checkpointer + `add_messages` accumulate per thread (canonical pattern; avoids dup from resending
  full history). Resume path unchanged.
- **New `compact_history_node`** placed `START → compact_history → supervisor`: when over
  `short_term_max_messages`, `trim_messages` to `short_term_keep_recent` and summarize the trimmed
  tail into `running_summary` (emit `RemoveMessage`s). **No-LLM fast path** under threshold; offline
  fallback = trim-only.
- **Planner conversation-awareness** — a `_history_block(state)` (summary + recent messages) prepended
  in `_llm_draft`, mirroring the existing `_memory_block`/`_evidence_block`.
- **Config knobs**: `short_term_max_messages` (~20), `short_term_keep_recent` (~6), token budget.

**Open questions for team:** (a) is the ResponsesAgent client expected to resend full history (then
we *replace* messages per turn) or just the latest (then we *accumulate*)? — affects the dedup
strategy; (b) should the supervisor also be conversation-aware, or planner-only for now?

## WS2 — Checkpoint prune (retention)

No native TTL ⇒ build it.

- **New `scripts/prune_checkpoints.py`** — `--older-than-days N` (default 30) `--dry-run`, mirroring
  `scripts/grant_lakebase_permissions.py`. Find stale threads
  (`GROUP BY thread_id HAVING max((checkpoint->>'ts')::timestamptz) < now() - interval 'N days'`),
  delete across `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` in one txn. Also **sweep orphan
  `session-*`/`it-*` branches** past TTL so the 10-branch cap can't fill.
- **`databricks.yml`** — replace the commented `jobs:` placeholder with a `prune_checkpoints`
  serverless Python job on a daily schedule.

**Open questions for team:** retention window (30d?); should prune run per branch (it targets the
branch in the job's config) or only `production`?

## WS3 — Per-session sandbox branch  *(capstone; opt-in)*

The agent runs on a throwaway branch so a prompt-injected `DROP TABLE` / runaway write can only
damage the disposable branch — then we promote only the curated memories back to prod.

- **Flag**: `settings.enable_session_sandbox` and/or `custom_inputs.sandbox=true`. Default path stays
  the singleton — unchanged.
- **Branch lifecycle keyed to the thread** (survives HITL interrupt/resume): first request of a
  sandbox thread → `ensure_branch("session-<thread_id>")` off `production`; resume reuses it; the
  handler deletes it after a **terminal** commit. CoW clone means `hydrate_memory` reads prior prod
  memory for free.
- **Merge-back at commit**: pass the singleton (parent) store as `config[...]["promote_store"]`;
  `commit_node` writes its curated `build_memory_writes` to the session store **and** the parent
  store — **reusing `memory.write_memories`** (no new merge logic; sidesteps Postgres' lack of branch
  merge).
- **New `scripts/demo_session_sandbox.py`** — drives a sandbox run with a simulated destructive
  write, shows prod untouched, shows the curated memory promoted, shows the branch deleted.

**Open questions for team:** (a) per-session pool cost + ~seconds branch-create latency — acceptable
for the demo path only? (b) deletion policy if a sandbox thread is abandoned mid-HITL (rely on TTL +
WS2 sweep); (c) does the operational hybrid query also run on the session branch (CoW snapshot of
static seeded data — fine) or stay on prod?

## WS4 — Integration tests (on the branch-per-test harness)

All `@pytest.mark.integration`, gated by env flag; built incrementally as each WS lands:
- Store round-trip on real Lakebase (embeddings, `store_vectors.field_name='memory_text'`, recall).
- Graph two-turn cross-session recall + **HITL interrupt/resume** on a real checkpointer+store.
- Multi-turn: messages accumulate per thread; compaction triggers a `running_summary`.
- Prune: seed an old `ts`, run prune, assert deletion.
- Sandbox: fork → write on branch → assert parent store **unaffected** → assert promotion → branch
  deleted.

## Recommended order of operations

`WS0 (foundation)` → `WS1 (multi-turn)` → `WS2 (prune)` → `WS3 (sandbox)` → `WS4 (tests, incremental)`.
WS0 is first because both WS3 and WS4 depend on the branching helper. WS1/WS2 are independent and can
proceed in parallel once WS0 lands.

## Risks / watch-outs (carried from research)

- **Per-session pool cost + branch-create latency** — opt-in only; never the default path.
- **Branch cap (10 unarchived/project)** — every fork needs TTL + teardown; WS2 sweeps orphans.
- **Sandbox spans interrupt/resume** — branch keyed to thread, deleted only on terminal commit.
- **`add_messages` dedup** — send only the new turn per request; integration-test accumulation.
- **Messages serde** — verify LangChain messages + `RemoveMessage` round-trip the contract-aware
  serde used by `AsyncCheckpointSaver`.
- **App→DB binding grant needs a workspace admin** (`CAN_MANAGE` on the instance is not enough — this
  already bit the mfg + credit DAIS booth apps).
- **Checkpoint/eval serde warning:** the eval harness uses a plain `MemorySaver` without
  `_contract_aware_serde`, so it logs benign "Deserializing unregistered type" warnings (incl.
  `MemoryContext`). Optional cleanup: apply the contract serde in `evaluate_agent.py`.

## References

- Phase 1 code: commit `efd8e73` (branch `ws/long-term-memory-and-branching`).
- Lakebase dev/prod + ownership runbook: `data/operational/README.md` → "Agent memory: dev vs prod".
- Auth / deploy-first note: `CLAUDE.md` (Lakebase credentials section).
- Internal: *Enterprise Lakebase Design Guide* (branching topologies); app-templates
  `agent-langgraph-advanced` (lifespan-singleton store/checkpointer, PR #219).
