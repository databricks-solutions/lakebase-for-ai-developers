#!/usr/bin/env bash
# Deploy the chat UI + agent as a Databricks App on the shared yau46e workspace, via the
# direct `databricks apps` path (no DABs bundle). Run from the repo root.
#
#   ./scripts/deploy_app_yau46e.sh
#
# Prereqs:
#   • frontend/dist is BUILT:  npm --prefix frontend install && npm --prefix frontend run build
#   • profile `mfg-sc-agent` authed to https://fevm-serverless-stable-yau46e.cloud.databricks.com
#   • app.yaml present at repo root (runtime manifest with the yau46e env)
set -euo pipefail

PROFILE="${PROFILE:-mfg-sc-agent}"
APP_NAME="${APP_NAME:-supply-chain-planner}"
USER_EMAIL="$(databricks current-user me -p "$PROFILE" -o json | python3 -c 'import sys,json;print(json.load(sys.stdin)["userName"])')"
SRC="/Workspace/Users/${USER_EMAIL}/apps/${APP_NAME}"

if [ ! -d frontend/dist ]; then
  echo "ERROR: frontend/dist not found. Build it first:"
  echo "  npm --prefix frontend install && npm --prefix frontend run build"
  exit 1
fi

echo "==> 1/4 create the app (idempotent)"
databricks apps get "$APP_NAME" -p "$PROFILE" >/dev/null 2>&1 \
  || databricks apps create "$APP_NAME" -p "$PROFILE"

echo "==> 2/4 sync source to $SRC (respects .gitignore: skips node_modules, dist, .env)"
databricks sync --full . "$SRC" -p "$PROFILE"
# dist is git-ignored, so sync skipped it — upload the built SPA explicitly:
echo "    uploading built SPA (frontend/dist)…"
databricks workspace import-dir ./frontend/dist "$SRC/frontend/dist" --overwrite -p "$PROFILE"

echo "==> 3/4 deploy"
databricks apps deploy "$APP_NAME" --source-code-path "$SRC" -p "$PROFILE"

echo "==> 4/4 app service principal (grant it data access — see scripts/grant_app_sp.sh)"
databricks apps get "$APP_NAME" -p "$PROFILE" -o json \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("  service principal:",d.get("service_principal_client_id"),d.get("service_principal_name"));print("  url:",d.get("url"))'

echo ""
echo "Done. Open the URL above, then /ui for the chat interface."
echo "If the agent errors on Lakebase, run: ./scripts/grant_app_sp.sh   (you are databricks_superuser, so you can)"
