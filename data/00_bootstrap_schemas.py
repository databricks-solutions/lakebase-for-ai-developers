"""Bootstrap the Unity Catalog schemas + volume the demo needs, idempotently.

First task of the setup_and_seed job. Assumes `uc_catalog` already exists and is writable
(see docs/DEPLOY.md) — this only creates the schemas/volume *within* it:

  - <uc_catalog>.<uc_schema>                      operational Delta tables + Knowledge corpus
  - <uc_catalog>.<uc_schema>.<uc_volume>          Knowledge source documents (PDFs)

(The MLflow UC trace schema is NOT created here — it's a declarative
`resources.schemas.mlflow_trace_schema` bundle resource in databricks.yml, created + granted to
the App's service principal in the same `bundle deploy` as the app itself.)

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

# The synced operational tables (03_sync_to_lakebase) register at
# <lakebase_uc_catalog>.<lakebase_operational_schema>.<table>; create-synced-table needs that UC
# schema to already exist (create_database_objects_if_missing makes the table, not the schema).
lb_catalog = settings.lakebase_uc_catalog or settings.uc_catalog
lb_schema = getattr(settings, "lakebase_operational_schema", None) or "public"

stmts = [
    f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{op_schema}`",
    f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{op_schema}`.`{volume}`",
    f"CREATE SCHEMA IF NOT EXISTS `{lb_catalog}`.`{lb_schema}`",
]

for s in stmts:
    print(f"-> {s}")
    spark.sql(s)

print(
    f"Bootstrap complete: {catalog}.{op_schema} (+ volume {volume}), "
    f"lakebase schema {lb_catalog}.{lb_schema}."
)
