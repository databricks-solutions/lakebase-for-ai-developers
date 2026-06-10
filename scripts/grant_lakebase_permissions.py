"""Grant a principal access to a Lakebase agent-memory schema.

Two real uses for this project's dev/prod memory split (see data/operational/README.md →
"Agent memory: dev vs prod"):

  * `--mode read`   — let your dev user *inspect* the SP-owned PROD memory schema without owning
                      it (read-only debugging of prod approvals/preferences/supplier-notes).
  * `--mode langgraph` — let the App service principal own/write a memory schema (USAGE+CREATE +
                      full DML + default privileges) — e.g. to repair a schema the SP can't write.

Must be run by an identity that can grant on the target schema (the schema owner or a
`databricks_superuser`; project creators already qualify). Connects via the same OAuth path as the
data-gen scripts (`data/operational/_lakebase.connect`) — so it targets **whatever Lakebase branch
your current `.env`/config points at** (`LAKEBASE_AUTOSCALING_BRANCH`). To grant on `production`,
point your config at the production branch first. GRANTs are idempotent.

Usage:
    uv run python scripts/grant_lakebase_permissions.py <principal> [--schema S] [--mode read|langgraph]

Examples:
    # Let me read the SP-owned prod memory schema for debugging:
    uv run python scripts/grant_lakebase_permissions.py alex.miller@databricks.com \
        --schema supply_chain_planner_memory --mode read

    # Give the App SP full access to a memory schema (client id from `databricks apps get`):
    uv run python scripts/grant_lakebase_permissions.py <sp-client-id> \
        --schema supply_chain_planner_memory --mode langgraph
"""

from __future__ import annotations

import argparse
import os
import sys

# Local only: load .env before importing settings (mirrors the data-gen scripts; CLAUDE.md).
if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):
    from dotenv import load_dotenv

    load_dotenv()

from agent_server.config import settings  # noqa: E402
from data.operational._lakebase import connect  # noqa: E402


def _grants(schema: str, principal: str, mode: str) -> list[str]:
    """Schema-qualified GRANTs. Principal is double-quoted so emails / client-ids are safe."""
    p = f'"{principal}"'
    if mode == "read":
        return [
            f"GRANT USAGE ON SCHEMA {schema} TO {p}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {p}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {p}",
        ]
    # langgraph: the checkpointer/store create tables on setup() and read+write rows.
    return [
        f"GRANT USAGE, CREATE ON SCHEMA {schema} TO {p}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {p}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {p}",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("principal", help="Grantee: a user email or an App SP client id.")
    ap.add_argument(
        "--schema",
        default=settings.lakebase_memory_schema,
        help="Target memory schema (default: settings.lakebase_memory_schema = %(default)s).",
    )
    ap.add_argument("--mode", choices=["read", "langgraph"], default="read")
    ap.add_argument("--dry-run", action="store_true", help="Print the GRANTs without running them.")
    args = ap.parse_args()

    stmts = _grants(args.schema, args.principal, args.mode)
    print(f"Target schema : {args.schema}")
    print(f"Principal     : {args.principal}")
    print(f"Mode          : {args.mode}\n")
    for s in stmts:
        print(f"  {s};")

    if args.dry_run:
        print("\n(dry-run) nothing executed.")
        return 0

    with connect() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            for s in stmts:
                cur.execute(s)
    print("\nGrants applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
