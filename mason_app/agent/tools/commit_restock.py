"""Approval-gated supply-chain action: commit a restock / mitigation for a part.

Listed in REQUIRE_APPROVAL (agent/agent.py), so when the agent decides to call this the run
pauses and emits an `interrupt` event — the UI's approval panel surfaces it, and the human
approves/rejects before it executes. This mirrors the repo's planner → gate → commit HITL step,
but on Mason's built-in HumanInTheLoopMiddleware + managed session checkpointer.
"""

from langchain_core.tools import tool


@tool
def commit_restock(part_number: str, quantity: int, supplier: str) -> str:
    """Commit a restock / mitigation order for a part. Consequential — requires human approval
    before it runs. Use after recommending a quantity and supplier for a part with a shortage.

    Args:
        part_number: The part/SKU to restock.
        quantity: Units to order.
        supplier: Supplier to order from.
    """
    return f"Committed restock: {quantity} units of {part_number} from {supplier}."
