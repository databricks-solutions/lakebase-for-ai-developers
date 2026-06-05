"""Spark session for the data layer — works locally and on Databricks.

Lives in `data/` (NOT `agent_server/`): only the data-gen / setup scripts use Spark. The agent app
never touches Spark, and `databricks-connect` is a dev-only dependency that must never be installed
on Databricks (it conflicts with the runtime pyspark).

Use `get_spark()` instead of a bare `spark` global so the same file runs both ways:
- **On Databricks** (notebook/job): returns the ambient runtime session.
- **Local**: Databricks Connect, authenticated via `DATABRICKS_CONFIG_PROFILE`. For serverless,
  set `serverless_compute_id = auto` in your CLI profile (or `DATABRICKS_SERVERLESS_COMPUTE_ID`).

Environment detection reuses `settings.on_databricks` (the same DBR/Apps signal that gates dotenv).
"""

from __future__ import annotations

from agent_server.config import settings


def get_spark():
    """Return a SparkSession: ambient on Databricks, Databricks Connect locally."""
    if settings.on_databricks:
        from pyspark.sql import SparkSession

        return SparkSession.builder.getOrCreate()
    # Local only — imported lazily so Databricks never imports databricks-connect.
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.getOrCreate()
