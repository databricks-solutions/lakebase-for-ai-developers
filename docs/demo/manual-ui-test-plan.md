# Meridian UI — Manual Test Plan (local)

How to exercise the end-to-end demo locally: the chat → **structured plan** → per-action **Review/HITL** →
**commit write-back** → **Lakebase** tab → MLflow trace → cross-session **memory recall** loop.

Reuses the existing adhesive/Henkel seed data (Henkel = SUP-001 primary, DuPont = SUP-002 alternate,
SKU-1001 structural adhesive, on-hand 40, open POs 800, coverage gap 760). The Meridian narrative is a
relabel onto this data — there is no IGBT data.

---

## 0. Bring it up

**Backend** (port 8000), from repo root:
```bash
# Real data (full demo — real Henkel incidents, real PO citations, Genie/VS):
DATABRICKS_CONFIG_PROFILE=mfg-sc-agent uv run start-server
# OR offline/deterministic fallback (stub gather data — works without seeded tables/VS/Genie):
USE_STUBS=1 DATABRICKS_CONFIG_PROFILE=mfg-sc-agent uv run start-server
```
> If port 8000 is busy, free it or run uvicorn on another port:
> `… uv run uvicorn agent_server.start_server:app --host 127.0.0.1 --port 8011` (then use that port below).

**Frontend** — two options:
- **Dev (hot reload, recommended for UI work):** `cd frontend && npm install && npm run dev` → open **http://localhost:5173** (Vite proxies `/api` + `/invocations` → :8000).
- **Built (what ships):** `cd frontend && npm run build`, then open **http://localhost:8000/ui/** (the server serves `dist/`).

**Pre-flight (once):**
1. **Identity** — the sidebar footer shows your email (OBO on the deployed app; your profile user locally). Operational reads run as the app service principal, so the hero scenario works for any signed-in user — no scope setup needed.
2. **Seed prior decisions** (for the memory-recall beat) — visit once:
   **http://localhost:5173/api/_seed_demo_memories** (dev) or **http://localhost:8000/api/_seed_demo_memories** (built). Expect `{"written": [...3 items]}`. Idempotent.
3. **Real-data sanity (skip if USE_STUBS):** open the **Inspect backend** drawer → "Peek inside" the pgvector card; you should see `quality_incidents` rows. If empty, either run the `data/` setup scripts or fall back to `USE_STUBS=1`.

> **Note on stub vs real:** under `USE_STUBS=1` the planner can't cite real PO ids (`po_id` null) or per-action `cost_delta`, and the plan is thinner. Use **real data** for the compelling run.
> **Note on LLM variance:** routing/grounding/plan composition are LLM-driven — exact actions and routes can vary run to run. The acceptance criteria below describe the *shape*, not exact strings.

---

## 1. Smoke checks

- [ ] `curl -s localhost:8000/api/me` returns your email.
- [ ] Frontend loads; **TopNav** shows **Chat · Review · Lakebase**.
- [ ] Chat is the default surface; the 5 suggestion chips render.
- [ ] Switching tabs works; Review/Lakebase show sensible **empty states** before any run.

---

## 2. Core end-to-end demo path (the happy path)

This is the 2-minute run-of-show. Do it in order.

**Step 1 — Trigger the exception (Chat).** Click the suggestion or type:
> *Similar quality issues for Henkel, joined to on-hand inventory and open POs*

- [ ] Live step progress streams (Routing → Running the operational query → Recalling prior decisions → Composing the recommendation).
- [ ] The route resolves to **operational** (optionally + analytics).
- [ ] The turn ends in an **approval cue** (status "awaiting approval"), not a plain answer.

**Step 2 — Review the plan (Review tab).**
- [ ] **3 evidence cards** render: **Data** (operational/analytics rows — on-hand 40, open POs), **RAG** (knowledge passages, may be empty depending on route), **Memory** (a recalled prior decision with a similarity score).
- [ ] **Structured action cards** render (expedite PO / split-source from DuPont / raise safety stock / allocation), each with **Approve / Hold**, an editable **quantity stepper**, and "writes to <table>".
- [ ] A **required rationale** textarea + a **Commit N writes** button + a live **ledger** on the right (staged amber).

**Step 3 — Exercise judgment (partial decisions).**
- [ ] **Approve** 2–3 actions; **Hold** one → the ledger drops the held one.
- [ ] **Edit** a quantity on an editable action → ledger reflects the new number.
- [ ] If a **raise-safety-stock** action is present + approved, drag the **slider** → value updates.
- [ ] Try **Commit with an empty/short rationale** → blocked (button disabled, or an error). Type a ≥12-char rationale → commit enabled.
- [ ] **Commit** → ledger flushes **amber → green**; a "See it in Lakebase" affordance appears.

**Step 4 — See what landed (Lakebase tab).**
- [ ] The **approved** actions appear as rows in `approved_actions` / `planning_parameters` / `constraints` (only the approved ones; held excluded).
- [ ] Rows carry your **rationale** and edited quantities.
- [ ] The **agent_memory** card shows the decision was embedded (this run's decision sits beside the recalled prior one).
- [ ] Toggle **Engineer ↔ Planner** lens → same rows render as raw columns/types vs. plain language.

**Step 5 — Prove it ran (Trace).**
- [ ] An **"Open in MLflow ↗"** link is present (from `trace_id`); it opens the workspace trace UI.

**Step 6 — Close the loop (memory recall).** New conversation → ask:
> *Continue this morning's escalation*

- [ ] The planner **recalls the seeded prior decision** (Henkel SKU-1001 coverage gap) and builds on it — visible in the recommendation/reasoning and the Memory evidence card.

---

## 3. Routing matrix (what each question should do)

| # | Question | Expected route | UI behavior |
|---|----------|----------------|-------------|
| A | *Similar quality issues for Henkel … joined to on-hand inventory and open POs* | operational (+analytics) | **action-bearing → Review/HITL** |
| B | *Henkel SKU-1001 adhesive cracking — recommend a mitigation given inventory, open POs, and total open POs for Q4* | operational + analytics | **action-bearing → Review/HITL** |
| C | *Nucor announced a carbon-steel price increase — find related market notes and similar incidents, recommend a pre-buy* | knowledge + operational | **action-bearing → Review/HITL** |
| D | *Total unfulfilled demand by product code for Q4/Q1?* | analytics | **informational → plain chat answer, no Review** |
| E | *What is the total open PO quantity by supplier for Q4?* | analytics | informational → chat answer |
| F | *Which suppliers are currently flagged at risk?* | analytics | informational → chat answer |
| G | *What do our Caterpillar and Lockheed Martin contracts say about late-delivery penalties?* | knowledge | informational → chat answer |
| H | *Have we seen a disruption like the PrecisionBond recall before?* | knowledge (market events) | informational → chat answer |
| I | *What did we decide about the Acme delay yesterday?* | (memory recall) | recalls seeded Acme/DuPont bridge-order decision |
| J | *Continue this morning's escalation* | (memory recall) | recalls seeded Henkel SKU-1001 escalation |

Acceptance: A–C surface the **Review** path; D–H answer **in chat with no approval card**; I–J **recall** seeded memory (requires step 0.2).

---

## 4. Negative / edge cases

- [ ] **Required rationale guard:** commit with no rationale is blocked (UI) and the server rejects an approve-resume lacking a rationale.
- [ ] **Partial commit:** approve 1 of N → exactly 1 set of rows lands; held actions write nothing.
- [ ] **Edited quantity persists:** the edited qty (not the proposed one) is what appears in the Lakebase row.
- [ ] **Informational ≠ Review:** a D–H question does **not** show action cards or a commit button.
- [ ] **Empty states:** a fresh thread (no run / no commit) shows the Review "no plan awaiting" and Lakebase "nothing committed yet" states (Henkel framing, not IGBT).
- [ ] **New conversation / reset:** starting a new conversation clears the Review surface.
- [ ] **Re-commit idempotency (optional):** approving the same thread twice updates rows in place rather than duplicating (keyed by thread_id).

---

## 5. Cleanup (after testing against real Lakebase)

Each committed run writes real rows (write-back tables in `public` + the embedded decision in the memory store). To reset the demo DB between runs, delete by thread, or wipe all manual rows:
```sql
-- write-back tables (operational schema = public)
DELETE FROM public.approved_actions     WHERE thread_id = '<thread>';
DELETE FROM public.planning_parameters  WHERE thread_id = '<thread>';
DELETE FROM public.constraints          WHERE thread_id = '<thread>';
-- embedded decision (memory schema); keys = thread_id (approvals/preferences) or 'thread:supplier'
DELETE FROM supply_chain_planner_memory.store_vectors WHERE key LIKE '<thread>%';
DELETE FROM supply_chain_planner_memory.store         WHERE key LIKE '<thread>%';
```
(Thread ids are visible in the Lakebase tab / the `done` frame.) The 3 write-back tables themselves should stay — they're part of the app.

---

## 6. Acceptance summary

The demo passes when: an operational question produces a **structured, grounded multi-action plan**; the human can **approve / edit / hold per action** and **must** record a rationale; **commit writes the approved rows** (with rationale + edits) to Lakebase and **embeds the decision**; the **Lakebase tab** shows exactly those rows in both lenses; the **trace** link opens; and a **follow-up conversation recalls** the decision — the loop closes on screen.
