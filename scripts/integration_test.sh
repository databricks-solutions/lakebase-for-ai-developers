#!/usr/bin/env bash
# integration_test.sh — full cold-start E2E for the Supply-Chain Planner Copilot, in an isolated
# git worktree, with always-safe teardown.
#
# WHAT IT PROVES: that `make deploy` cold-starts cleanly on a BRAND-NEW Lakebase project + UC schema —
# i.e. the genie_spaces table-validation ordering is correct (empty tables created before bundle
# deploy) and the deploy is genuinely one-shot. A green run = the cold-start path works end to end.
#
# WHY A WORKTREE: a detached worktree has its own clean `.databricks/` state, so a test deploy never
# mixes bundle/resource ids with your real dev/demo deploy. It tests COMMITTED code (HEAD by default).
#
# WHY THROWAWAY PROJECT + SCHEMA (the safety model):
#   - Throwaway Lakebase project → the app SP only ever owns memory/write-back schemas inside a project
#     we delete wholesale at teardown, so `bundle destroy` (which deletes the app + SP and ORPHANS those
#     schemas) is always safe here. See docs/lakebase-apps-permissions.md for why orphaned SP-owned
#     schemas are otherwise unrecoverable.
#   - Throwaway UC schema → tables don't pre-exist, so the run actually exercises the cold-start
#     (genie validation) path instead of passing trivially against already-seeded tables.
# Teardown runs via an EXIT trap, so even a mid-run failure cleans up (unless --keep).
#
# Usage:
#   scripts/integration_test.sh --profile <p> --uc-catalog <existing-writable-catalog> [options]
#
# Options:
#   --profile, -p <name>       Databricks CLI profile (required).
#   --uc-catalog <name>        EXISTING, writable UC catalog for the test (required). A fresh schema is
#                              created inside it; the catalog itself is never created or deleted.
#   --target, -t <dev|demo|byo>  DABs target (default: dev). Use `byo` + --sql-warehouse-id on a
#                              workspace where you can't create a warehouse.
#   --sql-warehouse-id <id>    Existing warehouse id (required for --target byo).
#   --lakebase-project <id>    Throwaway project id (default: scp-itest-<epoch>). Auto-created + deleted.
#   --uc-schema <name>         Throwaway schema name (default: scp_itest_<epoch>). Created + dropped.
#   --ref <git-ref>            Git ref to test in the worktree (default: HEAD — current commit).
#   --no-seed                  Skip the demo-data seed (faster; still proves deploy + cold-start).
#   --keep                     Leave the worktree + all resources up for debugging (NO teardown).
#   --force-teardown           Pass --force to itest_teardown.py (delete even without the itest marker).
#   -h, --help                 Show this help.
#
# Prereqs: same as `make deploy` (CLI ≥ 1.3.0, auth, uv, node) PLUS serverless_compute_id=auto in the
# profile (the cold-start table DDL + the schema-drop teardown use Databricks Connect). The profile
# needs: create a Lakebase project, CREATE SCHEMA/TABLE on --uc-catalog, and (dev/demo) create a SQL
# warehouse — or use --target byo with --sql-warehouse-id.

set -euo pipefail

PROFILE="${DATABRICKS_CONFIG_PROFILE:-}"
UC_CATALOG=""
TARGET="dev"
SQL_WAREHOUSE_ID=""
LAKEBASE_PROJECT=""
UC_SCHEMA=""
REF="HEAD"
SEED=true
KEEP=false
FORCE_TEARDOWN=false

# ── Args ──────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile|-p)        PROFILE="$2"; shift 2 ;;
    --uc-catalog)        UC_CATALOG="$2"; shift 2 ;;
    --target|-t)         TARGET="$2"; shift 2 ;;
    --sql-warehouse-id)  SQL_WAREHOUSE_ID="$2"; shift 2 ;;
    --lakebase-project)  LAKEBASE_PROJECT="$2"; shift 2 ;;
    --uc-schema)         UC_SCHEMA="$2"; shift 2 ;;
    --ref)               REF="$2"; shift 2 ;;
    --no-seed)           SEED=false; shift ;;
    --keep)              KEEP=true; shift ;;
    --force-teardown)    FORCE_TEARDOWN=true; shift ;;
    -h|--help)           grep '^#' "$0" | grep -v '^#!' | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $1 (try --help)" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

info() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

[[ -n "$PROFILE" ]]    || die "set --profile <cli-profile>"
[[ -n "$UC_CATALOG" ]] || die "set --uc-catalog <existing-writable-catalog>"
[[ "$TARGET" != "byo" || -n "$SQL_WAREHOUSE_ID" ]] || die "--target byo requires --sql-warehouse-id <id>"

# Throwaway names carry the 'itest' marker so teardown's safety guard will act on them. A single epoch
# stamp keeps the project + schema correlated for one run.
TS="$(date +%s)"
[[ -n "$LAKEBASE_PROJECT" ]] || LAKEBASE_PROJECT="scp-itest-${TS}"
[[ -n "$UC_SCHEMA" ]]        || UC_SCHEMA="scp_itest_${TS}"

export DATABRICKS_CONFIG_PROFILE="$PROFILE"
# The cold-start table DDL (A1) and the schema-drop teardown use Databricks Connect, which needs a
# serverless compute id. Default it to `auto` so the harness works on any serverless-enabled workspace
# WITHOUT requiring `serverless_compute_id` in the CLI profile. Only Databricks Connect reads this var;
# the CLI/SDK calls ignore it. Override by exporting DATABRICKS_SERVERLESS_COMPUTE_ID before running.
export DATABRICKS_SERVERLESS_COMPUTE_ID="${DATABRICKS_SERVERLESS_COMPUTE_ID:-auto}"

# Bundle var overrides for every `databricks bundle` / `make deploy` call (deploy.sh helpers read
# these via `bundle validate --output json`, so the throwaway coords flow into ensure_lakebase_project,
# the cold-start table DDL, the Genie JSON, the synced tables, and the seed).
VAR_FLAGS=(--var "uc_catalog=$UC_CATALOG" --var "uc_schema=$UC_SCHEMA" --var "lakebase_project=$LAKEBASE_PROJECT")
[[ "$TARGET" == "byo" ]] && VAR_FLAGS+=(--var "sql_warehouse_id=$SQL_WAREHOUSE_ID")
MAKE_VARS="uc_catalog=$UC_CATALOG uc_schema=$UC_SCHEMA lakebase_project=$LAKEBASE_PROJECT"
[[ "$TARGET" == "byo" ]] && MAKE_VARS="$MAKE_VARS sql_warehouse_id=$SQL_WAREHOUSE_ID"

# verify_deploy.py + itest_teardown.py are LOCAL processes that read coords from the environment (not
# from bundle --var), so mirror the throwaway coords into the env too. project = throwaway; branch /
# endpoint / database come from the bundle's RESOLVED vars (honoring --var) so the local verify
# connects exactly where the app does — their config defaults are None, so they must be set explicitly.
export LAKEBASE_AUTOSCALING_PROJECT="$LAKEBASE_PROJECT"
export UC_CATALOG="$UC_CATALOG"
export UC_SCHEMA="$UC_SCHEMA"
_BUNDLE_JSON="$(databricks bundle validate -t "$TARGET" --profile "$PROFILE" "${VAR_FLAGS[@]}" --output json 2>/dev/null || echo '{}')"
_bv() { printf '%s' "$_BUNDLE_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin); v=d.get('variables',{}).get('$1',{})
print(v.get('value', v.get('default','')) if isinstance(v,dict) else v)" 2>/dev/null || true; }
export LAKEBASE_AUTOSCALING_BRANCH="$(_bv lakebase_branch)"
export LAKEBASE_AUTOSCALING_ENDPOINT="$(_bv lakebase_endpoint)"
export LAKEBASE_DATABASE="$(_bv lakebase_database)"

WORKTREE=""
TORN_DOWN=false

# ── Teardown (EXIT trap → runs on success, failure, and Ctrl-C) ──────────────────
teardown() {
  $TORN_DOWN && return 0
  TORN_DOWN=true
  if $KEEP; then
    warn "--keep set: leaving everything up. Clean up by hand when done:"
    [[ -n "$WORKTREE" ]] && printf '    (cd %s && databricks bundle destroy -t %s -p %s --auto-approve %s)\n' \
      "$WORKTREE" "$TARGET" "$PROFILE" "${VAR_FLAGS[*]}"
    printf '    uv run python scripts/itest_teardown.py --project %s --catalog %s --schema %s\n' \
      "$LAKEBASE_PROJECT" "$UC_CATALOG" "$UC_SCHEMA"
    [[ -n "$WORKTREE" ]] && printf '    git worktree remove --force %s\n' "$WORKTREE"
    return 0
  fi

  info "Teardown (throwaway project + schema → always safe)"
  # 1. Destroy the in-bundle resources (app + SP, experiment, job, dev/demo warehouse, Genie space).
  if [[ -n "$WORKTREE" && -d "$WORKTREE" ]]; then
    (cd "$WORKTREE" && databricks bundle destroy -t "$TARGET" --profile "$PROFILE" --auto-approve \
      "${VAR_FLAGS[@]}") || warn "bundle destroy failed (delete the app by hand if it lingers)"
  fi
  # 2. Drop the throwaway UC schema + delete the throwaway Lakebase project (nukes orphaned SP schemas).
  local force_flag=()
  $FORCE_TEARDOWN && force_flag=(--force)
  uv run python scripts/itest_teardown.py --project "$LAKEBASE_PROJECT" \
    --catalog "$UC_CATALOG" --schema "$UC_SCHEMA" ${force_flag[@]+"${force_flag[@]}"} \
    || warn "itest_teardown.py reported leftovers — see above"
  # 3. Remove the worktree.
  if [[ -n "$WORKTREE" ]]; then
    git worktree remove --force "$WORKTREE" 2>/dev/null || warn "could not remove worktree $WORKTREE"
  fi
  ok "teardown done"
}
trap teardown EXIT

# ── Preflight ───────────────────────────────────────────────────────────────────
info "Cold-start integration test"
printf '  profile=%s  target=%s  catalog=%s  schema=%s  project=%s  ref=%s  seed=%s\n' \
  "$PROFILE" "$TARGET" "$UC_CATALOG" "$UC_SCHEMA" "$LAKEBASE_PROJECT" "$REF" "$SEED"

if ! git diff --quiet HEAD 2>/dev/null; then
  warn "working tree has uncommitted changes — the worktree tests COMMITTED code ($REF). Commit first if you want them tested."
fi

# ── 1. Isolated worktree ──────────────────────────────────────────────────────────
info "Create isolated worktree (clean .databricks state) at \$REF=$REF"
WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/scp-itest-XXXXXX")"
git worktree add --detach "$WORKTREE" "$REF" || die "git worktree add failed"
ok "worktree: $WORKTREE"

# ── 2. uv sync (dev deps incl. databricks-connect, in the worktree) ────────────────
info "uv sync in the worktree"
(cd "$WORKTREE" && uv sync) || die "uv sync failed in the worktree"
ok "deps synced"

# ── 3. Cold-start deploy (creates the throwaway project + schema + tables, then the app + Genie) ──
info "make deploy (TARGET=$TARGET) — the cold-start under test"
SEED_ARG=(); $SEED || SEED_ARG=(SEED=false)
make -C "$WORKTREE" deploy PROFILE="$PROFILE" TARGET="$TARGET" VARS="$MAKE_VARS" ${SEED_ARG[@]+"${SEED_ARG[@]}"} \
  || die "make deploy FAILED — the cold-start path is broken (this is the core assertion). See output above."
ok "make deploy succeeded — cold-start deploy works"

# ── 4. Verify deployed state ───────────────────────────────────────────────────────
info "Verify deployed state"
APP_NAME="$(databricks bundle validate -t "$TARGET" --profile "$PROFILE" "${VAR_FLAGS[@]}" --output json 2>/dev/null \
  | python3 -c "import json,sys
d=json.load(sys.stdin)
for a in d.get('resources',{}).get('apps',{}).values(): print(a.get('name','')); break" 2>/dev/null | head -1 || true)"
[[ -n "$APP_NAME" ]] && ok "app: $APP_NAME" || warn "could not resolve app name"

CHECKS_OK=true

# app compute ACTIVE
if [[ -n "$APP_NAME" ]]; then
  state=""
  for ((i=0; i<36; i++)); do
    state=$(databricks apps get "$APP_NAME" --profile "$PROFILE" --output json 2>/dev/null \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('compute_status',{}).get('state',''))" 2>/dev/null || true)
    [[ "$state" == "ACTIVE" ]] && break
    sleep 10
  done
  [[ "$state" == "ACTIVE" ]] && ok "app compute ACTIVE" || { warn "app compute state: ${state:-unknown}"; CHECKS_OK=false; }
fi

# Genie space created (best-effort; the deploy creating it without error is the real signal)
GENIE_TITLE="$(databricks bundle validate -t "$TARGET" --profile "$PROFILE" "${VAR_FLAGS[@]}" --output json 2>/dev/null \
  | python3 -c "import json,sys
d=json.load(sys.stdin)
for s in d.get('resources',{}).get('genie_spaces',{}).values(): print(s.get('title','')); break" 2>/dev/null | head -1 || true)"
if [[ -n "$GENIE_TITLE" ]]; then
  if GENIE_TITLE_ENV="$GENIE_TITLE" uv run python -c "
import os, sys
from databricks.sdk import WorkspaceClient
title = os.environ['GENIE_TITLE_ENV']
try:
    spaces = WorkspaceClient().genie.list_spaces()
    items = getattr(spaces, 'spaces', spaces) or []
    names = [getattr(s, 'title', getattr(s, 'name', '')) for s in items]
    sys.exit(0 if any(title == n for n in names) else 3)
except Exception as e:
    print(f'   .. genie list skipped: {e}'); sys.exit(2)
" 2>/dev/null; then
    ok "Genie space present: $GENIE_TITLE"
  else
    warn "Genie space '$GENIE_TITLE' not confirmed via list_spaces (check manually)"
  fi
fi

# verify_deploy.py — Lakebase perm contract (env coords already exported above)
if [[ -n "$APP_NAME" ]]; then
  if APP_NAME="$APP_NAME" uv run python scripts/verify_deploy.py; then
    ok "verify_deploy passed"
  else
    warn "verify_deploy reported failures (above)"; CHECKS_OK=false
  fi
fi

# ── Result ──────────────────────────────────────────────────────────────────────
if $CHECKS_OK; then
  info "PASS — cold-start deploy + health checks green."
else
  info "DEPLOY OK, but one or more health checks failed — investigate the ⚠ lines above."
fi
# Teardown runs next via the EXIT trap (unless --keep).
$CHECKS_OK || exit 1
