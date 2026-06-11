# Spec — Meridian quality-containment pivot + reliable plan + chat handoff

## Why
Live testing showed the Review page empty and a generic chat approval card. Root cause: the seeded
operational data (`data/operational/seeds.py` Cluster A) is a **quality-defect** scenario (Henkel
SUP-001/SKU-1001 epoxy cracking), but the structured `ActionKind` taxonomy + Review UI were built for
the mockup's **shortage** scenario (expedite/split/safety/allocation). The planner grounds correctly
in the quality evidence → proposes "quality hold / quarantine," which don't map to the shortage kinds,
so it fills free-text `actions` but leaves structured `planned_actions = []`. `ReviewPanel.tsx:79`
renders empty when `planned_actions` is empty; the chat inline card shows the generic
"This action needs sign-off" and its Approve does a rationale-less *quick approve* that bypasses the
whole Review value prop.

This spec does three things (no data re-seed):
1. **Embrace the quality story** — broaden the action taxonomy to quality containment so the plan maps
   to the data and renders in Review.
2. **Make `planned_actions` reliable** — deterministic fallback so Review is never empty when a
   recommendation exists.
3. **Fix the chat→Review handoff** — the inline card becomes a "Review & decide →" CTA carrying the
   real summary; the rationale-less quick-approve bypass is removed.

Keep the existing shortage kinds (they still serve a shortage question). Additive + back-compat.

---

## SHARED CONTRACT (backend + frontend must match exactly)

New `ActionKind` enum values (added to the existing 4), with their write-back routing:

| ActionKind value | playbook | target_table | notes |
|---|---|---|---|
| `expedite_po` (existing) | shortage | approved_actions | |
| `split_source` (existing) | shortage/quality | approved_actions | reused for "bridge order from alternate (DuPont)" |
| `raise_safety_stock` (existing) | shortage | planning_parameters | parameter = `safety_stock` |
| `allocation_constraint` (existing) | both | constraints | constraint kind = `allocation` |
| **`quality_hold`** (NEW) | quality | approved_actions | hold N on-hand units pending validation |
| **`quarantine_po`** (NEW) | quality | approved_actions | quarantine/inspect an incoming PO |
| **`tighten_inspection`** (NEW) | quality | planning_parameters | parameter = `inspection_level` |
| **`supplier_quality_hold`** (NEW) | quality | constraints | constraint kind = `supplier_hold` (rule: hold the supplier's SKU until validated) |

Frontend `KIND_LABEL` (ReviewPanel) + the `ActionKind` TS union must include the 4 new values:
`quality_hold`→"Quality hold", `quarantine_po`→"Quarantine", `tighten_inspection`→"Inspection",
`supplier_quality_hold`→"Supplier hold".

The canonical quality plan (Henkel cracking) now maps to all 3 tables:
1. `quality_hold` — hold the 40 on-hand units → approved_actions
2. `quarantine_po` — quarantine the incoming 500-unit SUP-001 PO → approved_actions
3. `tighten_inspection` — raise inspection level → planning_parameters
4. `supplier_quality_hold` — no SUP-001/SKU-1001 until validated → constraints
(+ optional `split_source` bridge order from DuPont → approved_actions)

---

## BACKEND (owns `agent_server/`, not `evaluation/`)

### 1. `contracts.py` — add the 4 new `ActionKind` values (verbatim names above). No other contract
change. `ActionKind` is already serde-registered (enum), so no `lakebase.py` change needed.

### 2. `graph/planner.py`
- **`_KIND_TO_TABLE`**: add the 4 new kinds → their `target_table` per the table above.
- **`_to_planned_actions`**: set per-kind editability + qty labels/bounds for the new kinds:
  - `quality_hold`: editable, `qty_label="Units to hold"`, `qty_min=0`, `qty_step=10` (default qty = on-hand from operational rows if the LLM didn't give one).
  - `quarantine_po`: editable, `qty_label="PO units"`, `qty_min=0`, `qty_step=50`.
  - `tighten_inspection`: editable, `qty_label="Inspection %"`, `qty_min=0`, `qty_max=100`, `qty_step=5`.
  - `supplier_quality_hold`: `editable=False` (it's a rule); rely on `facts` (scope=SKU-1001, until="validated").
- **Reliable fallback (rec 1):** after `_to_planned_actions(draft, state)`, if it returns `[]` **and**
  `draft.actions` (free-text) is non-empty, synthesize structured actions from the text via a NEW pure
  helper `_planned_actions_from_text(actions: list[str], state) -> list[PlannedAction]`:
  - keyword→kind map (case-insensitive substring): `"quarantine"`→`quarantine_po`; `"inspect"`→
    `tighten_inspection`; `"hold"`→`quality_hold`; `"expedite"`→`expedite_po`;
    `"split"|"alternate"|"dupont|bridge"`→`split_source`; `"safety stock"`→`raise_safety_stock`;
    else default `quality_hold`.
  - title = the text line; detail = the text line; `qty=None`; `editable=False`; unique key per index;
    `target_table` from `_KIND_TO_TABLE`. Goal: Review is never empty when actions exist.
  - Wire it in `planner_node`: `planned = _to_planned_actions(draft, state) or _planned_actions_from_text(draft.actions, state)`. Keep `rec.actions = [a.title for a in planned]` when `planned`.
- **Prompt (`_SYSTEM_PROMPT` `<structured_plan>`):** explain BOTH playbooks and when to use which —
  *quality* questions (recurring defects, cracking, failures) → `quality_hold` / `quarantine_po` /
  `tighten_inspection` / `supplier_quality_hold` (+ `split_source` to re-source); *shortage* questions
  (coverage gap, supplier delay, force-majeure) → `expedite_po` / `split_source` /
  `raise_safety_stock` / `allocation_constraint`. Add a worked QUALITY example (Henkel cracking → the 4
  quality actions). Instruct: ALWAYS populate `planned_actions` (don't return only free-text).
- `_fallback_draft`: change its single deterministic action to a `quality_hold` (matches the hero data).

### 3. `operational_db.py` — `build_writeback_rows` (keep it the pure, testable helper):
- approved_actions rows: handle the new approved_actions kinds (quality_hold, quarantine_po) — store
  `kind` = the ActionKind value (already does this generically; just ensure new kinds flow through).
- planning_parameters rows: derive `parameter` from kind — `{raise_safety_stock:'safety_stock',
  tighten_inspection:'inspection_level'}` (default `'safety_stock'`); `new_value` = edited_qty ??
  safety_stock_override ?? qty.
- constraints rows: derive constraint `kind` column from action kind — `{allocation_constraint:
  'allocation', supplier_quality_hold:'supplier_hold'}` (default `'allocation'`).
- No DDL change (the 3 tables already have the needed columns).

### 4. Tests — `tests/test_meridian.py` (extend; keep existing green):
- `_to_planned_actions`: new kinds map to correct `target_table`.
- `_planned_actions_from_text`: free-text "Quarantine incoming PO" / "Quality hold on 40 units" →
  structured actions with `quarantine_po` / `quality_hold` and the right tables; empty text → [].
- `planner_node` fallback path: a draft with `actions=[...]`, `planned_actions=[]` yields a
  recommendation whose `planned_actions` is non-empty (Review-never-empty guarantee).
- `build_writeback_rows`: `tighten_inspection`→planning_parameters.parameter='inspection_level';
  `supplier_quality_hold`→constraints.kind='supplier_hold'.

### Backend verification
`uv run ruff check agent_server/ tests/` · `uv run pytest tests/ -q` (all green) · an offline
`USE_STUBS=1` planner_node smoke asserting a quality-flavored draft yields populated `planned_actions`.
Do NOT touch `agent_server/evaluation/`, `frontend/`, `data/`. No live Lakebase needed.

---

## FRONTEND (owns `frontend/`)

### 1. `src/types.ts` — extend the `ActionKind` union with the 4 new string values (exact names above).

### 2. `src/components/ReviewPanel.tsx`
- Add the 4 new `KIND_LABEL` entries (above).
- Relabel copy toward quality containment (light touch — keep the layout):
  - Evidence fallback copy + rationale placeholder: lean quality (e.g. rationale placeholder:
    "e.g. Mirror the prior SKU-1001 hold — quarantine the failing lot + incoming PO, tighten
    inspection, hold SUP-001 until adhesion/thermal validation passes.").
  - KPI strip: keep on-hand/open-POs/coverage-gap (still relevant context); the "Mitigation cost"
    stat stays.
- No logic change to the empty-state gate is required once the backend reliably populates
  `planned_actions`; leave `ReviewPanel.tsx:79` as is.

### 3. `src/components/ChatPanel.tsx` — rework the inline approval card (the `Extras` component, ~169-178):
- Header → "Plan ready for review".
- Body → show the REAL summary: `rec?.summary ?? appr.summary ?? "This plan needs your sign-off."`
  (`rec = extras.recommendation`).
- Replace the inline **Approve** with a primary **"Review & decide →"** CTA → calls a new
  `onGoToReview` prop. Keep a secondary **Reject** (quick reject) → `onResume("rejected")` (reject
  writes no operational rows, so it's safe inline). **Remove the inline Approve** so the
  rationale-less quick-approve path is gone — approving happens only in Review (with rationale +
  per-action decisions).
- Accept a new prop `onGoToReview: () => void`.

### 4. `src/App.tsx`
- Pass `onGoToReview={() => setPage("review")}` into `<ChatPanel/>`.
- Keep `onResumeStructured` as the single commit path (Review). Keep the binary `onResume` ONLY for
  the inline **Reject** (verdict "rejected"); it no longer handles approve. (You may give reject a
  clearer placeholder rationale, e.g. "(rejected from chat)".)
- `hasPausedPlan`/`pausedRecommendation` wiring stays.

### Frontend verification
`cd frontend && npm run build` (tsc --noEmit + vite) MUST pass. The `ActionKind` union must match the
backend enum values exactly. Do NOT touch `agent_server/` or `data/`.

---

## Non-goals / out of scope
- No data re-seed (reuse the adhesive/Henkel quality data).
- Keep the shortage kinds + shortage prompt branch (a shortage question still works).
- Eval scorer changes are optional/deferred (the grounded judge already handles quality plans).
- No new write-back tables or DDL.

## Acceptance
The hero question ("similar quality issues for Henkel … joined to inventory and open POs") yields a
**structured 4-action quality plan** that renders in **Review** (never empty), each action mapped to a
write-back table; the chat card is a **"Review & decide →"** CTA showing the real summary (no
rationale-less approve); committing from Review writes the quality rows to `approved_actions` /
`planning_parameters` (inspection_level) / `constraints` (supplier_hold) and they appear in the
Lakebase tab. `uv run pytest tests/ -q` and `npm run build` both green.
