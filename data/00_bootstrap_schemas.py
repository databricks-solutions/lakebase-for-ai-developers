"""Bootstrap the Unity Catalog schemas + volume the demo needs, idempotently.

First task of the setup_and_seed job. Assumes `uc_catalog` already exists and is writable
(see docs/DEPLOY.md) — this only creates the schemas/volume *within* it:

  - <uc_catalog>.<uc_schema>                      operational Delta tables + Knowledge corpus
  - <uc_catalog>.<uc_schema>.<uc_volume>          Knowledge source documents (PDFs)
  - <uc_catalog>.<mlflow_trace_schema>            MLflow 3 UC-backed trace tables (App writes here)

All `IF NOT EXISTS`, so re-runs (and overlap with genie/01, which also creates the operational
schema) are a no-op. Runs both ways via get_spark(): ambient on Databricks, Databricks Connect
locally. Reads catalog/schema from the single config source (agent_server.config.settings).
"""

import sys
from pathlib import Path

try:
    REPO_ROOT = str(Path(__file__).resolve().parents[1])
except NameError:
    REPO_ROOT = str(Path.cwd().resolve())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent_server.config import settings
from data._spark import get_spark

spark = get_spark()

catalog = settings.uc_catalog
op_schema = settings.uc_schema
volume = settings.uc_volume
# Trace catalog defaults to uc_catalog (config comment); schema is its own (default mlflow_traces).
trace_catalog = settings.mlflow_trace_catalog or settings.uc_catalog
trace_schema = settings.mlflow_trace_schema or "mlflow_traces"

stmts = [
    f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{op_schema}`",
    f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{op_schema}`.`{volume}`",
    f"CREATE SCHEMA IF NOT EXISTS `{trace_catalog}`.`{trace_schema}`",
]

for s in stmts:
    print(f"-> {s}")
    spark.sql(s)

print(
    f"Bootstrap complete: {catalog}.{op_schema} (+ volume {volume}), "
    f"trace schema {trace_catalog}.{trace_schema}."
)
