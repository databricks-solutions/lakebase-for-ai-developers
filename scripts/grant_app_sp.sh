#!/usr/bin/env bash
# Grant the deployed App's service principal access to the yau46e Lakebase operational data,
# and add OBO testers to the access list so the hero scenario returns rows for them.
# You can run this because you're a member of databricks_superuser on this branch.
#
#   ./scripts/grant_app_sp.sh                      # grants SP + adds you to user_access
#   TESTERS="you@databricks.com,other@..." ./scripts/grant_app_sp.sh
set -euo pipefail

PROFILE="${PROFILE:-mfg-sc-agent}"
APP_NAME="${APP_NAME:-supply-chain-planner}"
BRANCH="projects/mfg-supply-chain-copilot/branches/production"
EP="$BRANCH/endpoints/primary"
SCHEMA="public"
MEM_SCHEMA="supply_chain_planner_memory_app"
SCOPE="${SCOPE:-adhesives}"   # the hero cluster's access scope
ME="$(databricks current-user me -p "$PROFILE" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["userName"])')"
TESTERS="${TESTERS:-$ME}"

# 1) App service principal (its client id is its Postgres role name on Lakebase).
SP="$(databricks apps get "$APP_NAME" -p "$PROFILE" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin).get("service_principal_client_id",""))')"
[ -n "$SP" ] || { echo "Could not resolve app service principal — is the app deployed?"; exit 1; }
echo "app SP: $SP"

# Register the SP as a Postgres role on the branch (no-op if it already exists). If this errors,
# add the role via the Lakebase UI (Compute → the instance → Roles) and re-run the GRANTs below.
databricks postgres create-role "$BRANCH" --json "{\"role\":{\"name\":\"$SP\"}}" -p "$PROFILE" 2>/dev/null \
  && echo "registered SP as a Postgres role" || echo "create-role skipped (likely already exists / UI needed)"

# 2) Connect as you (superuser) and grant.
HOST="$(databricks postgres get-endpoint "$EP" -p "$PROFILE" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"]["hosts"]["host"])')"
export PGPASSWORD="$(databricks postgres generate-database-credential --json "{\"endpoint\":\"$EP\"}" -p "$PROFILE" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')"

TESTER_INSERTS=""
IFS=',' read -ra ARR <<< "$TESTERS"
for u in "${ARR[@]}"; do
  TESTER_INSERTS+="INSERT INTO ${SCHEMA}.user_access(user_id, scope) VALUES ('${u}','${SCOPE}') ON CONFLICT DO NOTHING;"$'\n'
done

psql "host=$HOST dbname=databricks_postgres user=$ME sslmode=require" <<SQL
-- operational reads for the app SP
GRANT USAGE ON SCHEMA ${SCHEMA} TO "${SP}";
GRANT SELECT ON ALL TABLES IN SCHEMA ${SCHEMA} TO "${SP}";
ALTER DEFAULT PRIVILEGES IN SCHEMA ${SCHEMA} GRANT SELECT ON TABLES TO "${SP}";
-- let the app SP create + own its agent-memory schema at startup
GRANT CREATE ON DATABASE databricks_postgres TO "${SP}";
-- add OBO testers to the access list so the hero scenario resolves for them
${TESTER_INSERTS}
SELECT 'user_access now:' AS note;
SELECT user_id, scope FROM ${SCHEMA}.user_access ORDER BY 1;
SQL

echo "Done. Restart/redeploy the app if it was already running so the SP picks up the grants."
