"""Canonical Genie space config — single source of truth for the Analytics agent's space.

Shared by:
- `02_create_genie_space.py` (creates/updates the space via `w.genie.create_space`)
- WS2/WS4 when they tune sample questions and instructions

Edit this file (not the UI) so changes survive teardown + rebuild. Once the space is created,
its `space_id` goes into `.env` as `GENIE_SPACE_ID`.

The serialized-space JSON shape follows version 2 of the Genie payload schema (the format
`w.genie.create_space(serialized_space=...)` consumes). Reference pattern:
https://github.com/databricks-solutions/devrel-examples/blob/main/demos/bee-pollinator/scripts/setup_agents.py
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from agent_server.config import settings


def _genie_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class TableSpec:
    name: str  # short name; rooted at catalog.schema by GenieSpaceConfig
    description: str  # one-paragraph table doc Genie attaches as metadata


@dataclass(frozen=True)
class ExampleSQL:
    question: str
    sql: str  # use `{prefix}` as a placeholder for catalog.schema; resolved at build time


@dataclass(frozen=True)
class GenieSpaceConfig:
    display_name: str
    description: str  # also stored as the space's top-level description
    tables: list[TableSpec]
    sample_questions: list[str]  # shown in the UI as starter prompts
    instructions: str = ""  # natural-language guidance Genie attaches to every Q
    example_sqls: list[ExampleSQL] = field(default_factory=list)  # certified Q→SQL pairs

    @property
    def table_prefix(self) -> str:
        return f"{settings.uc_catalog}.{settings.uc_schema}"

    @property
    def fq_table_identifiers(self) -> list[str]:
        return [f"{self.table_prefix}.{t.name}" for t in self.tables]

    def build_serialized_space(self) -> str:
        """Render to the version-2 serialized_space JSON the SDK expects."""
        prefix = self.table_prefix

        payload = {
            "version": 2,
            "config": {
                "sample_questions": sorted(
                    [{"id": _genie_id(), "question": [q]} for q in self.sample_questions],
                    key=lambda x: x["id"],
                ),
            },
            "data_sources": {
                "tables": sorted(
                    [
                        {
                            "identifier": f"{prefix}.{t.name}",
                            "description": [t.description],
                        }
                        for t in self.tables
                    ],
                    key=lambda t: t["identifier"],
                ),
            },
            "instructions": {
                "text_instructions": (
                    [{"id": _genie_id(), "content": [self.instructions]}] if self.instructions else []
                ),
                "example_question_sqls": sorted(
                    [
                        {
                            "id": _genie_id(),
                            "question": [ex.question],
                            "sql": [ex.sql.format(prefix=prefix)],
                        }
                        for ex in self.example_sqls
                    ],
                    key=lambda x: x["id"],
                ),
            },
        }
        return json.dumps(payload)


# Canonical config for the Supply-Chain Planner's Analytics agent.
# Tables are placeholders — `01_create_operational_schema.py` creates them empty with the
# schema below; WS2 (Chandhana) fills them with real synthetic data.
SUPPLY_CHAIN_GENIE_SPACE = GenieSpaceConfig(
    display_name="Supply-Chain Planner — Analytics",
    description=(
        "Aggregation + reporting over governed supply-chain tables for the Planner Copilot. "
        "Use for unfulfilled-demand rollups, supplier-risk summaries, and on-hand vs open-PO "
        "comparisons. NOT for semantic similarity over unstructured docs (use the Knowledge "
        "agent) or row-level memory queries (use the Operational agent)."
    ),
    tables=[
        TableSpec(
            name="suppliers",
            description=(
                "Master data for upstream suppliers. Columns: supplier_id (PK), name, country, "
                "categories (comma-sep). Join key for purchase_orders and supplier_status."
            ),
        ),
        TableSpec(
            name="product_dim",
            description=(
                "SKU master. Columns: sku (PK), name, category, list_price. Join key for "
                "inventory and purchase_orders."
            ),
        ),
        TableSpec(
            name="inventory",
            description=(
                "Current on-hand stock per SKU per location. Columns: sku, location, "
                "on_hand_qty, last_updated. Use MAX(last_updated) per (sku, location) for "
                "the current snapshot."
            ),
        ),
        TableSpec(
            name="purchase_orders",
            description=(
                "Open and historical POs. Columns: po_id (PK), supplier_id, sku, qty, "
                "expected_date, status (one of: open, in_transit, delivered, cancelled). "
                "Filter status != 'cancelled' for fulfillment math unless asked otherwise."
            ),
        ),
        TableSpec(
            name="supplier_status",
            description=(
                "Rolling supplier risk + on-time score. Columns: supplier_id, status (one of: "
                "healthy, watch, at_risk), risk_score (0-100), last_updated. Use MAX(last_updated) "
                "per supplier_id for current state."
            ),
        ),
    ],
    sample_questions=[
        "What is the total open PO quantity by supplier for Q4?",
        "Which SKUs have on-hand quantity below 100 units?",
        "Rank suppliers by risk_score, showing only those with status = 'at_risk'.",
        "For SKU 'SKU-1001', what is the on-hand inventory vs the sum of open POs?",
        "How many open POs are expected this month, broken out by supplier?",
    ],
    instructions=(
        "When the user asks about 'fulfillment' or 'unfulfilled demand', interpret as "
        "(SUM(open_po qty) - SUM(on_hand qty)) per SKU. When they ask about a supplier by name, "
        "join through suppliers.name. Always exclude rows where purchase_orders.status = "
        "'cancelled' unless the user asks specifically about cancellations. Prefer the most "
        "recent supplier_status row (MAX(last_updated)) when reporting current risk. For "
        "inventory snapshots, use MAX(last_updated) per (sku, location)."
    ),
    example_sqls=[
        ExampleSQL(
            question="Total open PO quantity by supplier for Q4",
            sql=(
                "SELECT s.name AS supplier, SUM(po.qty) AS open_qty "
                "FROM {prefix}.purchase_orders po "
                "JOIN {prefix}.suppliers s ON s.supplier_id = po.supplier_id "
                "WHERE po.status = 'open' "
                "  AND po.expected_date BETWEEN '2026-10-01' AND '2026-12-31' "
                "GROUP BY s.name ORDER BY open_qty DESC"
            ),
        ),
        ExampleSQL(
            question="On-hand vs open POs for SKU 'SKU-1001'",
            sql=(
                "WITH inv AS ("
                "  SELECT sku, SUM(on_hand_qty) AS on_hand FROM {prefix}.inventory "
                "  WHERE sku = 'SKU-1001' GROUP BY sku"
                "), po AS ("
                "  SELECT sku, SUM(qty) AS open_po FROM {prefix}.purchase_orders "
                "  WHERE sku = 'SKU-1001' AND status = 'open' GROUP BY sku"
                ") "
                "SELECT inv.sku, inv.on_hand, po.open_po, (po.open_po - inv.on_hand) AS gap "
                "FROM inv FULL OUTER JOIN po ON inv.sku = po.sku"
            ),
        ),
        ExampleSQL(
            question="Suppliers at risk, ranked by risk_score",
            sql=(
                "WITH latest AS ("
                "  SELECT supplier_id, status, risk_score, "
                "         ROW_NUMBER() OVER (PARTITION BY supplier_id ORDER BY last_updated DESC) rn "
                "  FROM {prefix}.supplier_status"
                ") "
                "SELECT s.name, l.status, l.risk_score "
                "FROM latest l JOIN {prefix}.suppliers s ON s.supplier_id = l.supplier_id "
                "WHERE l.rn = 1 AND l.status = 'at_risk' "
                "ORDER BY l.risk_score DESC"
            ),
        ),
    ],
)
