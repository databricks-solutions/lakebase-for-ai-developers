"""Shared short-term conversation-history rendering (WS1).

The full message history is checkpointed per thread; these helpers render the recent PRIOR
turns into compact text for prompt context. Both the planner (`planner_node`) and the
supervisor/router (`supervisor_node`) use them so follow-ups resolve referents ("that SKU",
"their pricing terms") from earlier in the same conversation — and so the two callsites can
never drift apart.

Two shapes:
- `render_history` — User/Assistant transcript for the LLM callsites (planner + LLM router).
- `recent_user_text` — just the prior USER turns, for the deterministic keyword router, which
  must not be polluted by keywords echoed in the assistant's own recommendation summaries.
"""

from __future__ import annotations

from agent_server.config import settings
from agent_server.graph.state import AgentState


def _prior_turns(state: AgentState, keep_recent: int | None) -> list:
    """The recent prior messages, excluding the just-appended current question.

    The entrypoint appends a HumanMessage for the current turn before the graph runs, so the
    last message is always the current question — drop it and keep only the last `keep_recent`
    of what remains (older turns are dropped, not summarized)."""
    msgs = state.get("messages") or []
    prior = msgs[:-1]  # drop the just-appended HumanMessage for the current question
    if not prior:
        return []
    n = settings.short_term_keep_recent if keep_recent is None else keep_recent
    return prior[-n:]


def render_history(state: AgentState, keep_recent: int | None = None) -> str:
    """Render recent prior turns into a compact User/Assistant transcript for an LLM prompt.

    Returns "" when there is no prior history (e.g. turn 1), so callers' prompts are identical
    to the no-history path on the first turn."""
    recent = _prior_turns(state, keep_recent)
    if not recent:
        return ""
    lines = []
    for m in recent:
        role = "User" if getattr(m, "type", "") == "human" else "Assistant"
        content = m.content if isinstance(m.content, str) else str(m.content)
        lines.append(f"  {role}: {content[:300]}")
    return "Earlier in this conversation:\n" + "\n".join(lines)


def recent_user_text(state: AgentState, keep_recent: int | None = None) -> str:
    """Concatenated text of the recent prior USER turns only (no assistant turns, no current
    question). The keyword router scans this to resolve referential follow-ups without picking
    up keywords that the assistant happened to echo in a prior recommendation summary."""
    recent = _prior_turns(state, keep_recent)
    texts = []
    for m in recent:
        if getattr(m, "type", "") != "human":
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        texts.append(content)
    return "\n".join(texts)
