"""Render the canonical Genie space → data/genie/supply_chain.geniespace.json.

This is the build step for the `resources.genie_spaces` DABs resource. The resource references the
JSON via `file_path`, and DABs ships that file verbatim — it does NOT substitute `${var...}` inside
it. So the fully-qualified table identifiers (`uc_catalog.uc_schema.<table>`) must be baked in at
generation time. `genie_config.build_serialized_space()` reads UC_CATALOG / UC_SCHEMA from
`settings`, so run this with the same catalog/schema the deploy uses (scripts/deploy.sh does this
before `bundle deploy`, passing the resolved bundle vars).

Output is pretty-printed and deterministic (ids are content-derived — see genie_config._genie_id),
so re-running with unchanged config produces a byte-identical file → no spurious diff on redeploy.

    uv run python data/genie/build_geniespace_json.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from data.genie.genie_config import SUPPLY_CHAIN_GENIE_SPACE

OUTPUT_PATH = Path(__file__).resolve().parent / "supply_chain.geniespace.json"


def main() -> None:
    cfg = SUPPLY_CHAIN_GENIE_SPACE
    # build_serialized_space() returns a compact JSON string; round-trip through json so the file is
    # pretty-printed (reviewable diffs) and key-sorted (stable across runs).
    serialized = json.loads(cfg.build_serialized_space())
    OUTPUT_PATH.write_text(json.dumps(serialized, indent=2, sort_keys=True) + "\n")
    print(f"✓ Wrote {OUTPUT_PATH}")
    print(f"  table_prefix : {cfg.table_prefix}")
    print(f"  tables       : {cfg.fq_table_identifiers}")


if __name__ == "__main__":
    main()
