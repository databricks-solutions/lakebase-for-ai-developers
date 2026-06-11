"""Long-term memory policy: namespaces, curated writes, and scoped semantic recall.

Single source of truth shared by the write site (`commit_node`) and the read site
(`hydrate_memory_node`) so the namespace tuples, the curated `memory_text` composition, and
the embed-field choice never drift apart.

Design (per the approved plan):

- **Three memory types**, each in its own namespace so recall is targeted and prunable:
  - `("approvals", <user>)`     — audit trail of every committed decision (both verdicts).
  - `("preferences", <user>)`   — distilled planner preferences (approved action-bearing only).
  - `("supplier_notes", <sid>)` — cross-user learned facts about a supplier (approved only).
- **Curated embedding.** Every value carries a `memory_text` field; we embed *only* that field
  (`index=["memory_text"]`) instead of the whole JSON, so semantic recall matches on meaning
  rather than on `{"verdict": "approved"}` boilerplate. The store's per-put `index` arg overrides
  its construction-time default of `["$"]` (confirmed against `databricks_langchain` /
  `langgraph.store.base.BaseStore.put`).
- **Write policy is curated by type + verdict** so recall stays high-signal: audit everything, but
  only learn preferences/supplier-notes from approved, action-bearing outcomes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from agent_server.contracts import HITLDecision, HITLVerdict, MemoryItem
from agent_server.graph.state import AgentState

logger = logging.getLogger(__name__)

# The single JSON field we embed for semantic recall (vs the library default of the whole value).
MEMORY_TEXT_FIELD = "memory_text"
EMBED_INDEX = [MEMORY_TEXT_FIELD]


# ── Namespaces ────────────────────────────────────────────────────────────────────────────
# user_id is sanitized the same way the original commit_node did (Postgres-prefix friendly).

def _sanitize_user(user_id: Optional[str]) -> str:
    return (user_id or "unknown").replace(".", "-")


def approvals_ns(user_id: Optional[str]) -> tuple[str, str]:
    return ("approvals", _sanitize_user(user_id))


def preferences_ns(user_id: Optional[str]) -> tuple[str, str]:
    return ("preferences", _sanitize_user(user_id))


def supplier_notes_ns(supplier_id: str) -> tuple[str, str]:
    return ("supplier_notes", supplier_id)


# ── Write policy ──────────────────────────────────────────────────────────────────────────

@dataclass
class MemoryWrite:
    """One store.aput() to perform. `index` selects which fields get embedded for this item."""

    namespace: tuple[str, ...]
    key: str
    value: dict[str, Any]
    index: Optional[list[str]] = field(default=None)


def _approval_text(
    question: Optional[str],
    verdict: Optional[str],
    summary: Optional[str],
    rationale: Optional[str] = None,
) -> str:
    q = (question or "").strip() or "(no question)"
    base = f"{q} → {verdict or 'n/a'}: {summary or '(no recommendation)'}"
    # Carry the human's rationale into the embedded text so the recalled decision explains WHY.
    if rationale and rationale.strip():
        base += f" | rationale: {rationale.strip()}"
    return base


def _preference_text(rec, note: Optional[str], rationale: Optional[str] = None) -> str:
    """Distil an approved decision into a reusable planner preference (deterministic for now;
    an LLM-distilled version is a later refinement)."""
    base = f"Approved approach: {rec.summary}"
    if rec.actions:
        base += " | actions: " + "; ".join(rec.actions[:2])
    if note:
        base += f" | planner note: {note}"
    if rationale and rationale.strip():
        base += f" | rationale: {rationale.strip()}"
    return base


def _supplier_note_text(supplier_id: str, sku: Optional[str], rec, verdict: Optional[str]) -> str:
    return f"{supplier_id}/{sku or '?'}: {rec.summary} — {verdict or 'n/a'}"


def build_memory_writes(
    state: AgentState, decision: Optional[HITLDecision], thread_id: str
) -> list[MemoryWrite]:
    """Compute the curated set of long-term writes for a committed run.

    - Approvals: always (audit), both verdicts.
    - Preferences + supplier notes: only when APPROVED *and* the recommendation is action-bearing.
    """
    rec = state.get("recommendation")
    question = state.get("question")
    user_id = state.get("user_id", "unknown")
    verdict = decision.verdict.value if decision else None
    note = decision.note if decision else None
    rationale = decision.rationale if decision else None
    summary = rec.summary if rec else None

    writes: list[MemoryWrite] = []

    # 1) Approvals — always (audit trail + recallable curated text). Key by thread = idempotent.
    writes.append(
        MemoryWrite(
            namespace=approvals_ns(user_id),
            key=thread_id,
            value={
                "question": question,
                "recommendation": rec.model_dump() if rec else None,
                "verdict": verdict,
                "note": note,
                "rationale": rationale,
                MEMORY_TEXT_FIELD: _approval_text(question, verdict, summary, rationale),
            },
            index=EMBED_INDEX,
        )
    )

    approved = bool(decision and decision.verdict == HITLVerdict.APPROVED)
    action_bearing = bool(rec and rec.is_action_bearing)
    if not (approved and action_bearing and rec):
        return writes  # rejected / informational → audit only, don't learn

    # 2) Preferences — distilled, per user.
    writes.append(
        MemoryWrite(
            namespace=preferences_ns(user_id),
            key=thread_id,
            value={
                MEMORY_TEXT_FIELD: _preference_text(rec, note, rationale),
                "question": question,
                "source_thread": thread_id,
            },
            index=EMBED_INDEX,
        )
    )

    # 3) Supplier notes — one per distinct supplier surfaced operationally (cross-user).
    op = state.get("operational_result")
    seen: set[str] = set()
    for row in (op.rows if op else []) or []:
        sid = row.supplier_id
        if not sid or sid in seen:
            continue
        seen.add(sid)
        writes.append(
            MemoryWrite(
                namespace=supplier_notes_ns(sid),
                key=f"{thread_id}:{sid}",
                value={
                    MEMORY_TEXT_FIELD: _supplier_note_text(sid, row.sku, rec, verdict),
                    "supplier_id": sid,
                    "sku": row.sku,
                    "source_thread": thread_id,
                },
                index=EMBED_INDEX,
            )
        )

    return writes


async def write_memories(store, writes: Sequence[MemoryWrite]) -> dict[str, int]:
    """Apply writes; never raise (a memory failure must not fail the run). Returns per-type counts.

    Writes are issued concurrently: `AsyncDatabricksStore` extends LangGraph's
    `AsyncBatchedBaseStore`, so concurrent `aput`s coalesce into a single batch — the curated
    `memory_text` fields are embedded together in one round-trip instead of one per write. (A
    sequential `await` loop would force a separate embed+insert round-trip per item.)"""
    counts: dict[str, int] = {}
    if store is None or not writes:
        return counts

    async def _put(w: MemoryWrite) -> str:
        await store.aput(w.namespace, w.key, w.value, index=w.index)
        return w.namespace[0]

    results = await asyncio.gather(*(_put(w) for w in writes), return_exceptions=True)
    for w, res in zip(writes, results):
        if isinstance(res, Exception):  # pragma: no cover - defensive
            logger.warning("memory write to %s/%s failed: %s", w.namespace, w.key, res)
        else:
            counts[res] = counts.get(res, 0) + 1
    return counts


# ── Recall (scoped semantic search) ─────────────────────────────────────────────────────────

async def _search(
    store, namespace_prefix: tuple[str, ...], query: str, limit: int, threshold: Optional[float]
) -> list[MemoryItem]:
    try:
        items = await store.asearch(namespace_prefix, query=query, limit=limit)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("memory recall on %s failed: %s", namespace_prefix, exc)
        return []
    out: list[MemoryItem] = []
    for it in items:
        score = getattr(it, "score", None)
        if threshold is not None and score is not None and score < threshold:
            continue
        value = getattr(it, "value", None) or {}
        text = value.get(MEMORY_TEXT_FIELD)
        if not text:
            continue
        ns = getattr(it, "namespace", None)
        out.append(
            MemoryItem(
                text=text,
                score=score,
                namespace="/".join(ns) if ns else None,
                key=getattr(it, "key", None),
            )
        )
    return out


async def recall_preferences(store, user_id, query, limit, threshold) -> list[MemoryItem]:
    return await _search(store, preferences_ns(user_id), query, limit, threshold)


async def recall_approvals(store, user_id, query, limit, threshold) -> list[MemoryItem]:
    return await _search(store, approvals_ns(user_id), query, limit, threshold)


async def recall_supplier_notes(
    store, supplier_ids: Iterable[str], query, limit, threshold
) -> list[MemoryItem]:
    """Search each surfaced supplier's namespace (cross-user notes), merge, rank by score."""
    seen: set[str] = set()
    tasks = []
    for sid in supplier_ids:
        if not sid or sid in seen:
            continue
        seen.add(sid)
        tasks.append(_search(store, supplier_notes_ns(sid), query, limit, threshold))
    if not tasks:
        return []
    merged: list[MemoryItem] = []
    for batch in await asyncio.gather(*tasks):
        merged.extend(batch)
    merged.sort(key=lambda m: (m.score if m.score is not None else 0.0), reverse=True)
    return merged[:limit]
