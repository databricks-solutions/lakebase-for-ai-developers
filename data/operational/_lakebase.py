"""Lakebase connection + embedding helpers for the operational data-gen scripts.

Scoped to `data/operational/` for now (the pre-seed + verify scripts). WS1 may later consolidate
connection handling into the agent runtime (the `databricks-langchain` `AsyncLakebasePool` already
does this for the checkpointer/store); this helper covers the one-off psycopg path that data-gen
needs. Follows the `databricks-lakebase` skill's connectivity patterns.

Two helpers:
- `connect()`  → a psycopg connection to Lakebase Postgres (OAuth credential, `sslmode=require`).
- `embed()`    → 1024-d embeddings from the Databricks embedding endpoint (for the pgvector table).

Auth resolution order (first that applies wins):
  1. `LAKEBASE_PG_URL`                  — explicit URL (local dev, Pattern 3).
  2. App-injected `PGHOST` + `LAKEBASE_ENDPOINT` (Databricks App, Pattern 4).
  3. `LAKEBASE_AUTOSCALING_ENDPOINT`    — autoscaling "projects" endpoint (Pattern 1, w.postgres).
  4. `LAKEBASE_INSTANCE_NAME`           — provisioned instance (Pattern 1, w.database).
"""

from __future__ import annotations

import os

import psycopg

from agent_server.config import settings


def _ws():
    # Imported lazily so pure-Python importers of this module (e.g. linting) don't require the SDK.
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def _current_user(w=None) -> str:
    """The authenticated Databricks user — the single source for 'who am I' (the Postgres
    connection identity). Pass an existing client to avoid a second call."""
    return (w or _ws()).current_user.me().user_name


def _conn_params() -> dict:
    """Resolve host/dbname/user/password for psycopg from whichever auth form is configured."""
    # 1. Explicit URL — let psycopg parse it (local dev only).
    if settings.lakebase_pg_url:
        return {"conninfo": settings.lakebase_pg_url}

    w = _ws()
    username = _current_user(w)  # connection identity is ALWAYS the real OAuth caller
    dbname = settings.lakebase_database

    # 2. Databricks App — platform injects PG* env + LAKEBASE_ENDPOINT.
    if os.environ.get("PGHOST") and os.environ.get("LAKEBASE_ENDPOINT"):
        token = w.postgres.generate_database_credential(endpoint=os.environ["LAKEBASE_ENDPOINT"]).token
        return {
            "host": os.environ["PGHOST"],
            "port": int(os.environ.get("PGPORT", "5432")),
            "dbname": os.environ.get("PGDATABASE", dbname),
            "user": os.environ.get("PGUSER", username),
            "password": token,
            "sslmode": "require",
        }

    # 3. Autoscaling endpoint (projects model). The full resource path is
    #    projects/<project>/branches/<branch>/endpoints/<endpoint>, whose prefix is exactly the
    #    `branch` that 03_sync_to_lakebase.py builds from LAKEBASE_AUTOSCALING_PROJECT/BRANCH.
    #    Accept EITHER a complete path OR a bare endpoint id combined with that same project/branch
    #    pair — so one autoscaling config drives the whole 02→04 run (02/04 connect, 03 syncs)
    #    instead of forcing a redundant full-path var disjoint from project/branch.
    if settings.lakebase_autoscaling_endpoint:
        name = settings.lakebase_autoscaling_endpoint
        if not name.startswith("projects/"):
            project = settings.lakebase_autoscaling_project
            branch = settings.lakebase_autoscaling_branch
            if not (project and branch):
                raise RuntimeError(
                    "LAKEBASE_AUTOSCALING_ENDPOINT is a bare endpoint id, so also set "
                    "LAKEBASE_AUTOSCALING_PROJECT and LAKEBASE_AUTOSCALING_BRANCH (the same pair "
                    "03_sync_to_lakebase.py uses) — or set the full "
                    "projects/<project>/branches/<branch>/endpoints/<id> path."
                )
            name = f"projects/{project}/branches/{branch}/endpoints/{name}"
        endpoint = w.postgres.get_endpoint(name=name)
        token = w.postgres.generate_database_credential(endpoint=endpoint.name).token
        return {
            "host": endpoint.status.hosts.host,
            "dbname": dbname,
            "user": username,
            "password": token,
            "sslmode": "require",
        }

    # 4. Provisioned instance. NOTE: this path uses the `w.database.*` API and the
    # `instance.read_write_dns` / `generate_database_credential(instance_names=...)` shapes, which
    # are less exercised here than the autoscaling (`w.postgres.*`) path above — verify against your
    # databricks-sdk version before relying on it. The project default is autoscaling.
    if settings.lakebase_instance_name:
        instance = w.database.get_database_instance(name=settings.lakebase_instance_name)
        cred = w.database.generate_database_credential(
            request_id="data-gen", instance_names=[settings.lakebase_instance_name]
        )
        return {
            "host": instance.read_write_dns,
            "dbname": dbname,
            "user": username,
            "password": cred.token,
            "sslmode": "require",
        }

    raise RuntimeError(
        "No Lakebase connection configured. Set one of LAKEBASE_PG_URL, "
        "LAKEBASE_AUTOSCALING_ENDPOINT, or LAKEBASE_INSTANCE_NAME in .env "
        "(see .env.example and the databricks-lakebase skill)."
    )


def connect() -> psycopg.Connection:
    """Open a psycopg connection to Lakebase. Caller closes (or use as a context manager)."""
    params = _conn_params()
    if "conninfo" in params:
        return psycopg.connect(params["conninfo"])
    return psycopg.connect(**params)


def ensure_vector_ready(cur, *, create: bool = False) -> None:
    """Make the pgvector `vector` type + operators resolvable for operational-schema queries.

    The `vector` extension is database-global but its objects (the `vector` type, the `<=>`
    operator, `vector_cosine_ops`) live in exactly ONE schema. The agent-memory store
    (`AsyncDatabricksStore.setup`) runs `CREATE EXTENSION vector` in the MEMORY schema at app
    startup — which, on a fresh deploy, happens BEFORE the operational seed — so `vector` lands in
    the memory schema, not `public`. The operational session's search_path (`"$user", public`) then
    can't resolve unqualified `vector(...)` / `::vector` / `<=>`, and you get `type "vector" does not
    exist`. (On an already-seeded workspace it worked only because the extension already sat in
    `public`.) Discover where the extension actually lives and prepend it to the session search_path.
    `create=True` (seed only) installs the extension first if nothing has yet.
    """
    if create:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute("SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'vector'")
    row = cur.fetchone()
    op_schema = settings.lakebase_operational_schema
    ext_schema = row[0] if row else op_schema
    # operational schema first (its tables resolve unqualified too), then wherever pgvector lives.
    cur.execute(f"SET search_path TO {op_schema}, {ext_schema}, public")


def vector_literal(vec: list[float]) -> str:
    """pgvector text form '[1,2,3]' for casting `::vector`. Shared by 02 (seed insert) and 04
    (query vector) so the literal format can't drift between the write and read paths."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def embed(texts: list[str], *, endpoint: str | None = None) -> list[list[float]]:
    """Return one embedding per input text via the Databricks embedding serving endpoint.

    Used to populate the `quality_incidents.embedding vector(1024)` column. Lakebase pgvector has
    no managed-embeddings option — we compute here and INSERT `::vector` (see 02_pre_seed_pgvector).
    """
    endpoint_name = endpoint or settings.embedding_endpoint
    w = _ws()
    resp = w.serving_endpoints.query(name=endpoint_name, input=texts)
    # OpenAI-compatible shape: resp.data[i].{embedding, index}. Be tolerant of dict vs object.
    data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)
    if data is None:
        raise RuntimeError(f"Unexpected embedding response from {endpoint_name}: {resp!r}")

    def _field(item, name):
        val = getattr(item, name, None)
        if val is None and isinstance(item, dict):
            val = item.get(name)
        return val

    # Re-sort by the OpenAI-compatible `index`: batched/parallelized endpoints may return
    # `data` out of input order, and the caller (02_pre_seed_pgvector) zips these positionally
    # with the incident rows — so an unsorted response would silently mis-pair embeddings.
    # Fall back to arrival order for any item missing `index`.
    indexed = [
        (pos if _field(item, "index") is None else _field(item, "index"), list(_field(item, "embedding")))
        for pos, item in enumerate(data)
    ]
    indexed.sort(key=lambda t: t[0])
    return [vec for _, vec in indexed]
