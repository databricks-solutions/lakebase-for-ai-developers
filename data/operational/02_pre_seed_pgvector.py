"""02 — Pre-seed the `quality_incidents` pgvector table in Lakebase.

This is the ONE operational table that does not arrive via Synced Tables. A Delta `array<float>`
syncs to Postgres `jsonb` (not a real `vector`), and synced tables are read-only — so the vector
table is a NATIVE Lakebase Postgres table we write directly here. Lakebase pgvector has no
managed-embeddings option, so we compute embeddings via the Databricks embedding endpoint and
INSERT `::vector` (see the FGAC guide "Path C" and the databricks-lakebase `pgvector.md` skill).

Steps: CREATE EXTENSION vector → CREATE TABLE → embed each `description` → INSERT ::vector →
CREATE INDEX (HNSW, cosine). Idempotent: the table is recreated and reloaded from `seeds.py`, so
re-runs reproduce identical content.

Runs locally (`uv run python data/operational/02_pre_seed_pgvector.py`) or as a Databricks job;
auth + connection resolve via `_lakebase.connect()`. Requires Lakebase configured in `.env`.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent_server.config import settings
from data.operational import seeds
from data.operational._lakebase import connect, embed, ensure_vector_ready, vector_literal

SCHEMA = settings.lakebase_operational_schema
TABLE = f"{SCHEMA}.quality_incidents"


def main() -> None:
    rows = seeds.build_quality_incidents()
    print(f"Embedding {len(rows)} incident descriptions via '{settings.embedding_endpoint}'...")
    vectors = embed([r["description"] for r in rows])
    if len(vectors) != len(rows):
        sys.exit(f"Embedding count mismatch: {len(vectors)} vectors for {len(rows)} rows")
    dims = len(vectors[0])
    print(f"  got {len(vectors)} vectors, {dims} dims")

    with connect() as conn, conn.cursor() as cur:
        # Create the pgvector extension if needed AND put its schema on the search_path — on a fresh
        # deploy the app's memory store has already created it in the MEMORY schema, so `vector(...)`
        # below won't resolve from `public` otherwise. See ensure_vector_ready.
        ensure_vector_ready(cur, create=True)
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        # Recreate for idempotency (demo table we fully own).
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(f"""
            CREATE TABLE {TABLE} (
              incident_id   text PRIMARY KEY,
              supplier_id   text NOT NULL,
              sku           text NOT NULL,
              category      text NOT NULL,
              summary       text,
              description   text,
              severity      text,
              status        text,
              incident_date date,
              expired_at    timestamptz,
              embedding     vector({dims})
            )
        """)

        insert_sql = f"""
            INSERT INTO {TABLE}
              (incident_id, supplier_id, sku, category, summary, description,
               severity, status, incident_date, expired_at, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
        """
        for r, vec in zip(rows, vectors):
            # Superseded/closed a week AFTER the incident occurred (must be > incident_date).
            expired_at = (
                f"{(r['incident_date'] + timedelta(days=7)).isoformat()}T00:00:00Z"
                if r["expired"] else None
            )
            cur.execute(insert_sql, (
                r["incident_id"], r["supplier_id"], r["sku"], r["category"],
                r["summary"], r["description"], r["severity"], r["status"],
                r["incident_date"].isoformat(), expired_at, vector_literal(vec),
            ))

        # HNSW index for cosine similarity (built after load).
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_quality_incidents_embedding "
            f"ON {TABLE} USING hnsw (embedding vector_cosine_ops)"
        )
        cur.execute(f"ANALYZE {TABLE}")
        conn.commit()

        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
        total = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE expired_at IS NULL")
        active = cur.fetchone()[0]
        print(f"\n✓ {TABLE}: {total} rows ({active} active, {total - active} expired)")

        # Smoke test: the hero query text should surface Cluster A (Henkel / SKU-1001) on top.
        qvec = vector_literal(embed([seeds.HERO_QUERY_TEXT])[0])
        cur.execute(
            f"SELECT supplier_id, sku, summary, "
            f"       round((1 - (embedding <=> %s::vector))::numeric, 3) AS similarity "
            f"FROM {TABLE} WHERE expired_at IS NULL "
            f"ORDER BY embedding <=> %s::vector LIMIT 5",
            (qvec, qvec),
        )
        print("\nTop-5 for hero query (expect Henkel/SKU-1001 adhesive-cracking rows):")
        for sup, sku, summ, sim in cur.fetchall():
            print(f"  {sim}  {sup}/{sku}  {summ}")

    print("\nDone. Next: 03_sync_to_lakebase.py")


if __name__ == "__main__":
    main()
