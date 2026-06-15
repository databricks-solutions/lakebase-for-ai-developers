"""Offline tests for history-aware routing (the supervisor / router).

No workspace needed: the LLM route is either monkeypatched or forced to fall back, so every
test runs deterministically against the keyword router and the prompt-construction logic.

Coverage:
- `render_history` / `recent_user_text` helpers (shared with the planner).
- Single-turn routing is UNCHANGED (regression lock — protects the eval baseline).
- A bare referential follow-up routes correctly once history is available.
- The `_llm_route` prompt carries a <conversation_history> block iff prior turns exist.
- The `ROUTER_USE_HISTORY` flag cleanly disables the whole feature.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent_server.graph import supervisor
from agent_server.graph.history import recent_user_text, render_history

# A clean referential follow-up to a knowledge-domain turn: alone it matches no keyword;
# with the prior user turn it resolves to a contract (knowledge) question.
PRIOR_KNOWLEDGE_Q = "What do our Caterpillar contracts say about late-delivery penalties?"
FOLLOWUP_REFERENT_Q = "And their pricing terms?"


def _state(question: str, *turns) -> dict:
    """Build a state dict. `turns` are prior (role, text) pairs; the current question is
    appended as the trailing HumanMessage exactly as the entrypoint does."""
    msgs = []
    for role, text in turns:
        msgs.append(HumanMessage(content=text) if role == "user" else AIMessage(content=text))
    msgs.append(HumanMessage(content=question))
    return {"question": question, "messages": msgs}


# ── history helpers ───────────────────────────────────────────────────────────────────────

def test_render_history_excludes_current_and_keeps_prior():
    state = _state("current", ("user", "q1"), ("assistant", "a1"))
    out = render_history(state)
    assert "User: q1" in out and "Assistant: a1" in out
    assert "current" not in out  # the current question is excluded


def test_render_history_empty_on_first_turn():
    assert render_history(_state("only question")) == ""


def test_render_history_trims_to_keep_recent():
    state = _state(
        "current",
        ("user", "alpha"), ("assistant", "bravo"), ("user", "charlie"), ("assistant", "delta"),
    )
    out = render_history(state, keep_recent=2)
    assert "charlie" in out and "delta" in out
    assert "alpha" not in out and "bravo" not in out


def test_recent_user_text_excludes_assistant_turns():
    state = _state("current", ("user", "user-says"), ("assistant", "assistant-says"))
    out = recent_user_text(state)
    assert "user-says" in out
    assert "assistant-says" not in out  # keyword router must not see assistant summaries


# ── keyword router: single-turn regression lock ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "question, expected",
    [
        ("What do our Caterpillar contracts say about late-delivery penalties?", ["knowledge"]),
        ("What is the total open PO quantity by supplier for Q4 2026?", ["analytics"]),
        ("Find similar past quality incidents to Henkel's SKU-1001 adhesive cracking.", ["operational"]),
    ],
)
def test_keyword_route_single_turn_unchanged(question, expected):
    # No history → identical to the historical single-turn behavior.
    assert supervisor._keyword_route(question).agents == expected


# ── keyword router: history-aware follow-up ─────────────────────────────────────────────────

def test_keyword_route_bare_referent_resolves_via_history():
    decision = supervisor._keyword_route(FOLLOWUP_REFERENT_Q, history=PRIOR_KNOWLEDGE_Q)
    assert decision.agents == ["knowledge"]
    assert "conversation history" in decision.reasoning


def test_keyword_route_bare_referent_without_history_defaults():
    # Same question, no history → falls back to the both-surfaces default (today's behavior).
    decision = supervisor._keyword_route(FOLLOWUP_REFERENT_Q)
    assert decision.agents == ["knowledge", "analytics"]
    assert "conversation history" not in decision.reasoning


# ── supervisor_node end-to-end (keyword path) ───────────────────────────────────────────────

def test_supervisor_node_uses_history_on_followup(monkeypatch):
    monkeypatch.setattr(supervisor, "_llm_route", lambda *a, **k: None)  # force keyword fallback
    state = _state(FOLLOWUP_REFERENT_Q, ("user", PRIOR_KNOWLEDGE_Q), ("assistant", "Reviewed penalties."))
    out = supervisor.supervisor_node(state)
    assert out["route_decision"].agents == ["knowledge"]
    assert "history=used" in out["trace_notes"][-1]


def test_supervisor_node_flag_off_ignores_history(monkeypatch):
    monkeypatch.setattr(supervisor, "_llm_route", lambda *a, **k: None)
    monkeypatch.setattr(supervisor.settings, "router_use_history", False)
    state = _state(FOLLOWUP_REFERENT_Q, ("user", PRIOR_KNOWLEDGE_Q), ("assistant", "Reviewed penalties."))
    out = supervisor.supervisor_node(state)
    # With history disabled, the bare referent matches nothing → both-surfaces default.
    assert out["route_decision"].agents == ["knowledge", "analytics"]
    assert "history=none" in out["trace_notes"][-1]


# ── LLM route: prompt construction ──────────────────────────────────────────────────────────

def _capturing_chat():
    """A fake ChatDatabricks that records the user-message content and returns a fixed decision."""
    cap: dict = {}
    from agent_server.contracts import RouterDecision

    class _Chat:
        def __init__(self, **kwargs):
            pass

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            cap["user_content"] = messages[-1]["content"]
            return RouterDecision(agents=["operational"], reasoning="captured")

    return _Chat, cap


def test_llm_route_wraps_history_when_present(monkeypatch):
    pytest.importorskip("databricks_langchain")
    chat, cap = _capturing_chat()
    monkeypatch.setattr("databricks_langchain.ChatDatabricks", chat)
    supervisor._llm_route(FOLLOWUP_REFERENT_Q, history="Earlier:\n  User: " + PRIOR_KNOWLEDGE_Q)
    assert "<conversation_history>" in cap["user_content"]
    assert "<current_question>" in cap["user_content"]
    assert FOLLOWUP_REFERENT_Q in cap["user_content"]


def test_llm_route_bare_question_when_no_history(monkeypatch):
    pytest.importorskip("databricks_langchain")
    chat, cap = _capturing_chat()
    monkeypatch.setattr("databricks_langchain.ChatDatabricks", chat)
    supervisor._llm_route("Single-turn question?", history="")
    # First turn must be byte-identical to the legacy question-only prompt.
    assert cap["user_content"] == "Single-turn question?"
