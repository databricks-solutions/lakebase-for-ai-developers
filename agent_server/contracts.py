"""Pydantic I/O contracts for every gather agent + planner + router.

These are the Phase-0 contracts CLAUDE.md mandates. Change them only via team decision —
they're the interface every workstream builds against in isolation. Stubs and real
implementations must produce identical shapes so swapping in the real node is a no-op.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ── Document taxonomy ────────────────────────────────────────────────────────────────────

class DocType(str, Enum):
    """Maps 1:1 to subfolders under seed_data/bronze_documents/. Used as a VS metadata
    filter so the supervisor can scope retrieval (e.g. 'only contracts')."""

    CONTRACT = "contract"
    SUPPLIER_NOTIFICATION = "supplier_notification"
    COMPETITOR_CATALOG = "competitor_catalog"
    PROMOTION_BRIEF = "promotion_brief"
    MARKET_EVENT = "market_event"


# ── Knowledge agent (Vector Search over PDFs) ────────────────────────────────────────────

class KnowledgePassage(BaseModel):
    chunk_id: str
    source: str  # original PDF path or filename
    page: int | None = None
    doc_type: DocType
    doc_id: str | None = None  # e.g. 'CTR-2024-1000' or supplier name
    content: str
    score: float | None = None


class KnowledgeQuery(BaseModel):
    query: str
    doc_types: list[DocType] | None = None  # optional metadata filter
    k: int = 5


class KnowledgeResult(BaseModel):
    query: str
    passages: list[KnowledgePassage]


# ── Analytics agent (Genie) ──────────────────────────────────────────────────────────────

class GenieQuery(BaseModel):
    question: str
    conversation_id: str | None = None  # optional multi-turn handle


class GenieResult(BaseModel):
    """Per CLAUDE.md: the agent returns its generated SQL so the join + access scope are
    traceable and scorable."""

    question: str
    answer: str | None = None  # NL summary from Genie
    sql: str | None = None  # the SQL Genie produced
    rows: list[dict] | None = None  # tabular result, if any
    conversation_id: str | None = None
    error: str | None = None


# ── Operational agent (Lakebase hybrid query — WS2 owns the implementation) ───────────────

class OperationalQuery(BaseModel):
    question: str
    user_id: str  # OBO; drives access-scope predicate inside the SQL


class OperationalRow(BaseModel):
    """Whatever the hybrid SQL returns, normalized through this shape."""

    sku: str | None = None
    supplier_id: str | None = None
    summary: str | None = None
    similarity: float | None = None
    on_hand_qty: float | None = None
    open_po_qty: float | None = None
    extra: dict = Field(default_factory=dict)


class OperationalResult(BaseModel):
    question: str
    sql: str  # always emitted; traceability requirement
    rows: list[OperationalRow]


# ── Long-term memory (hydrated after gather fan-in, injected into the planner context) ────
# Curated semantic recall over the Lakebase LangGraph store. Write policy + namespaces live in
# `agent_server.memory`; the hydrate node (`graph/memory_nodes.py`) populates `MemoryContext`.

class MemoryItem(BaseModel):
    """One recalled long-term memory snippet (the curated `memory_text` + recall metadata)."""

    text: str  # the curated memory_text that was embedded + recalled
    score: float | None = None  # semantic similarity from the store search (higher = closer)
    namespace: str | None = None  # e.g. "approvals" / "preferences" / "supplier_notes"
    key: str | None = None


class MemoryContext(BaseModel):
    """Long-term memory hydrated before the planner runs. Distinct state key (no reducer)."""

    preferences: list[MemoryItem] = Field(default_factory=list)
    prior_approvals: list[MemoryItem] = Field(default_factory=list)
    supplier_notes: list[MemoryItem] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.preferences or self.prior_approvals or self.supplier_notes)


# ── Router / supervisor ──────────────────────────────────────────────────────────────────

# Long-term memory is NOT a routable gather agent — it's hydrated after the gather fan-in
# (see `graph/memory_nodes.py`), so the supervisor only chooses among the live gather agents.
AgentName = Literal["knowledge", "analytics", "operational"]


class RouterDecision(BaseModel):
    """Which gather agents to invoke. Multi-select — supervisor may fan out to all three."""

    agents: list[AgentName]
    reasoning: str | None = None  # for tracing


# ── Planner / gate / HITL ────────────────────────────────────────────────────────────────

class PlannerRecommendation(BaseModel):
    summary: str  # one-line recommendation
    actions: list[str]  # ordered steps the planner proposes
    needs_approval: bool  # gate verdict (set by code, not the LLM)
    is_action_bearing: bool = True  # commits spend / risky-irreversible vs purely informational
    est_cost_usd: float | None = None
    reasoning: str | None = None
    citations: list[str] = Field(default_factory=list)  # source paths / SQL refs


class HITLVerdict(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    # EDITED = "edited"  # P2; not yet


class HITLDecision(BaseModel):
    verdict: HITLVerdict
    note: str | None = None
    user_id: str
