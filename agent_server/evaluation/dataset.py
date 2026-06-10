"""Evaluation dataset — the sanity set the flywheel scores against.

A small, labelled set derived from the agent's purpose + the demo scenarios. Each record is
`{"inputs": {"query": ...}, "expectations": {...}}`:

- `expected_route` documents routing intent for the `routing_correctness` judge (not a hard label —
  an extra reasonable agent is acceptable).
- `expected_action_bearing` is the DETERMINISTIC gate label (does the recommendation commit spend /
  is it risky-irreversible, vs purely informational) — scored without an LLM by `gate_correctness`.
- `should_need_approval` mirrors it (the gate trips when action_bearing OR cost≥threshold, and no
  sanity question clears the cost threshold).

Operational SQL/join correctness is deferred until the Synced Tables are provisioned (then the
gather agents return real rows instead of stubs).
"""

from __future__ import annotations

EVAL_RECORDS = [
    {
        # Canonical demo question — phrased to ask for a recommendation so the gate moment is
        # unambiguous (a bare "show me …" is borderline; see docs/follow-ups.md eval gaps).
        "inputs": {"query": "Henkel's structural adhesive SKU-1001 has recurring quality issues — "
                            "show me the similar past cases joined to on-hand inventory and open POs, "
                            "and recommend a mitigation."},
        "expectations": {"expected_route": ["operational"],
                         "expected_action_bearing": True,
                         "should_need_approval": True,
                         "note": "Similar-cases + live inventory/PO join → operational; mitigation is actionable/costly → approve."},
    },
    {
        "inputs": {"query": "What is the total open PO quantity by supplier for Q4?"},
        "expectations": {"expected_route": ["analytics"],
                         "expected_action_bearing": False,
                         "should_need_approval": False,
                         "note": "Pure aggregation → analytics/Genie; informational → no approval needed."},
    },
    {
        "inputs": {"query": "What do our Caterpillar and Lockheed Martin contracts say about late-delivery penalties?"},
        "expectations": {"expected_route": ["knowledge"],
                         "expected_action_bearing": False,
                         "should_need_approval": False,
                         "note": "Document lookup → knowledge/Vector Search; informational."},
    },
    {
        "inputs": {"query": "Henkel SKU-1001 has recurring adhesive cracking. Recommend a mitigation "
                            "given on-hand inventory and open POs, and total open POs by supplier for Q4."},
        "expectations": {"expected_route": ["operational", "analytics"],
                         "expected_action_bearing": True,
                         "should_need_approval": True,
                         "note": "Similar cases + aggregation; mitigation (reorder/re-source) is costly → approve."},
    },
    {
        "inputs": {"query": "Which suppliers are currently flagged at risk?"},
        "expectations": {"expected_route": ["analytics"],
                         "expected_action_bearing": False,
                         "should_need_approval": False,
                         "note": "Status rollup → analytics; informational → no approval."},
    },
    {
        "inputs": {"query": "Nucor announced a carbon-steel price increase. Find related market-event "
                            "notes and similar past incidents, and recommend whether to pre-buy."},
        "expectations": {"expected_route": ["knowledge", "operational"],
                         "expected_action_bearing": True,
                         "should_need_approval": True,
                         "note": "Docs + similar incidents; a pre-buy is a costly commitment → approve."},
    },
]

__all__ = ["EVAL_RECORDS"]
