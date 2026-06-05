"""Create (or reuse) the Supply-Chain Planner Genie space programmatically.

Idempotent — looks up an existing space by title before creating. Tables and instructions
come from `genie_config.SUPPLY_CHAIN_GENIE_SPACE`, so changes to the space are made by
editing that file and re-running this script.

Runs locally (uv run python data/genie/02_create_genie_space.py) or as a Databricks
notebook/job. Requires `databricks-sdk >= 0.106.0` (for `w.genie.create_space`).

Reference SDK pattern:
https://github.com/databricks-solutions/devrel-examples/blob/main/demos/bee-pollinator/scripts/setup_agents.py

Output: prints the `space_id` to paste into `.env` as `GENIE_SPACE_ID`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from databricks.sdk import WorkspaceClient

from agent_server.config import settings
from data.genie.genie_config import SUPPLY_CHAIN_GENIE_SPACE


def _resolve_warehouse_id(w: WorkspaceClient) -> str:
    """Use the explicit env var if set; otherwise pick the first RUNNING warehouse (cheap +
    serverless if available). Auto-detection mirrors the Genie skill's behavior."""
    if settings.warehouse_id:
        return settings.warehouse_id

    warehouses = list(w.warehouses.list())
    if not warehouses:
        sys.exit(
            "No SQL warehouses available. Set DATABRICKS_WAREHOUSE_ID in .env or create one."
        )
    # Prefer RUNNING → STARTING → STOPPED, then by smallest size for cost.
    state_rank = {"RUNNING": 0, "STARTING": 1, "STOPPED": 2}
    size_rank = {"2X-Small": 0, "X-Small": 1, "Small": 2, "Medium": 3, "Large": 4}
    warehouses.sort(
        key=lambda w: (
            state_rank.get(getattr(w.state, "value", str(w.state)), 99),
            size_rank.get(w.cluster_size or "", 99),
        )
    )
    chosen = warehouses[0]
    print(f"Auto-selected warehouse: {chosen.name} ({chosen.id}, {chosen.cluster_size}, {chosen.state})")
    return chosen.id


def _find_existing_space_id(w: WorkspaceClient, title: str) -> str | None:
    try:
        resp = w.genie.list_spaces()
    except AttributeError:
        sys.exit(
            "Your databricks-sdk version doesn't expose w.genie.list_spaces / create_space. "
            "Upgrade: uv add 'databricks-sdk>=0.106.0' (or pip install -U databricks-sdk)."
        )
    for space in (resp.spaces or []):
        if getattr(space, "title", None) == title:
            return space.space_id
    return None


def main() -> None:
    cfg = SUPPLY_CHAIN_GENIE_SPACE
    print(f"Genie space     : {cfg.display_name}")
    print(f"Table identifiers: {cfg.fq_table_identifiers}")
    print(f"Profile          : {settings.databricks_profile or '(ambient)'}")

    w = WorkspaceClient()

    existing = _find_existing_space_id(w, cfg.display_name)
    if existing:
        print(f"\n✓ Genie space already exists: space_id={existing}")
        print(f"\nAdd to .env:\n  GENIE_SPACE_ID={existing}")
        return

    warehouse_id = _resolve_warehouse_id(w)
    serialized = cfg.build_serialized_space()

    print(f"\nCreating space '{cfg.display_name}'...")
    space = w.genie.create_space(
        warehouse_id=warehouse_id,
        title=cfg.display_name,
        description=cfg.description,
        serialized_space=serialized,
    )

    print(f"\n✓ Created: space_id={space.space_id}")
    print(f"\nAdd to .env:\n  GENIE_SPACE_ID={space.space_id}")


if __name__ == "__main__":
    main()
