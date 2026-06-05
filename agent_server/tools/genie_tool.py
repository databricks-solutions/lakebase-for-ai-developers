"""Analytics agent tool — Genie Conversation API wrapped as a LangGraph @tool.

Per CLAUDE.md: the agent returns its generated SQL so the join + access scope are
traceable and scorable. We surface the SQL alongside the NL answer and the row data.

Uses `w.genie.start_conversation_and_wait` (or `create_message_and_wait` for follow-ups)
to delegate the NL→SQL translation to the curated Genie space.
"""

from __future__ import annotations

from functools import lru_cache

from databricks.sdk import WorkspaceClient
from langchain_core.tools import tool

from agent_server.config import settings
from agent_server.contracts import GenieResult


@lru_cache(maxsize=1)
def _client() -> WorkspaceClient:
    """Lazy + cached. SDK credential chain picks up the right auth mode (profile vs
    ambient on Apps)."""
    return WorkspaceClient()


def _extract_sql_and_text(msg) -> tuple[str | None, str | None]:
    """Pull the SQL string and any NL text response out of a GenieMessage."""
    sql_str: str | None = None
    text_str: str | None = None
    for attachment in (getattr(msg, "attachments", None) or []):
        if getattr(attachment, "query", None) and getattr(attachment.query, "query", None):
            sql_str = attachment.query.query
        if getattr(attachment, "text", None) and getattr(attachment.text, "content", None):
            text_str = attachment.text.content
    return sql_str, text_str


def _extract_rows(w: WorkspaceClient, space_id: str, conv_id: str, msg) -> list[dict] | None:
    """If the message has a query attachment, fetch its rows as a list of dicts."""
    attachments = getattr(msg, "attachments", None) or []
    attachment_id = next(
        (a.attachment_id for a in attachments if getattr(a, "query", None)), None
    )
    if not attachment_id:
        return None
    try:
        result = w.genie.get_message_attachment_query_result(
            space_id=space_id, conversation_id=conv_id, message_id=msg.id,
            attachment_id=attachment_id,
        )
    except AttributeError:
        # Older SDK: fall back to the message-level query result endpoint.
        result = w.genie.get_message_query_result(
            space_id=space_id, conversation_id=conv_id, message_id=msg.id,
        )
    stmt = getattr(result, "statement_response", None)
    if not stmt:
        return None
    columns = [c.name for c in (stmt.manifest.schema.columns or [])] if stmt.manifest else []
    rows = (stmt.result.data_array or []) if stmt.result else []
    return [dict(zip(columns, row)) for row in rows]


def ask_genie_impl(question: str, conversation_id: str | None = None) -> GenieResult:
    """Typed entry point — used by the graph's gather node and by tests."""
    if not settings.genie_space_id:
        return GenieResult(question=question, error="GENIE_SPACE_ID is not set in env.")

    w = _client()
    space_id = settings.genie_space_id

    try:
        if conversation_id:
            msg = w.genie.create_message_and_wait(
                space_id=space_id, conversation_id=conversation_id, content=question,
            )
            conv_id = conversation_id
        else:
            resp = w.genie.start_conversation_and_wait(space_id=space_id, content=question)
            # SDK shape varies slightly across versions: some return the conv on the resp,
            # others on the message. Handle both.
            conv_id = getattr(resp, "conversation_id", None) or getattr(
                getattr(resp, "conversation", None), "id", None
            )
            msg = getattr(resp, "message", None) or resp
    except Exception as e:
        return GenieResult(question=question, error=f"Genie call failed: {e!r}")

    sql, text = _extract_sql_and_text(msg)
    rows = _extract_rows(w, space_id, conv_id, msg) if sql else None

    return GenieResult(
        question=question, answer=text, sql=sql, rows=rows, conversation_id=conv_id,
    )


@tool
def ask_genie(question: str, conversation_id: str | None = None) -> dict:
    """Ask a structured business question over the supply-chain operational tables
    (suppliers, inventory, purchase_orders, supplier_status, product_dim).

    Use this for aggregation / reporting questions: 'total open POs by supplier', 'SKUs
    below 100 units on hand', 'suppliers at risk ranked by risk_score'. Returns the SQL
    Genie generated plus the rows.

    Args:
        question: Natural-language question.
        conversation_id: Optional — pass the prior result's conversation_id to ask a
                         follow-up with context preserved.

    Returns:
        A dict with question, answer, sql, rows, conversation_id, and (on failure) error.
    """
    return ask_genie_impl(question, conversation_id).model_dump()
