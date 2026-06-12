#!/usr/bin/env bash
# deploy.sh — one-command deploy for the Supply-Chain Planner Copilot (Databricks Apps + Lakebase).
#
# Bulletproof, idempotent, cold-start-safe: hand it to another FE or a customer and they can run it
# against a fresh workspace. The same engine powers the full one-shot deploy AND the fast dev loop.
#
# Usage:
#   scripts/deploy.sh --profile <cli-profile> [--target dev|demo] [options]
#
# Common:
#   make deploy      PROFILE=<p>            # full one-shot (this script, no flags)
#   make redeploy    PROFILE=<p>            # fast: agent-server change → deploy + restart (--app-only)
#   make redeploy-ui PROFILE=<p>            # fast: frontend change → build + deploy + restart
#
# Options:
#   --profile, -p <name>        Databricks CLI profile (or export DATABRICKS_CONFIG_PROFILE).
#   --target,  -t <dev|demo>    DABs target (default: dev).
#   --no-seed                   Skip the demo-data seed job (bring your own data).
#   --no-verify                 Skip the post-deploy smoke check.
#   --app-only                  Fast path: only build(opt) + bundle deploy + bundle run + report.
#                               Skips preflight, lakebase project, seed, Genie. For code iteration.
#   --build-frontend            Force the SPA build (default ON for full deploy, OFF for --app-only).
#   --no-build-frontend         Skip the SPA build.
#   --genie-consumer-group <g>  Workspace group to grant CAN_RUN on the Genie space (OBO consumers).
#   --var k=v                   Extra bundle variable override (repeatable).
#   -h, --help                  Show this help.
#
# Phases (full deploy): 0 preflight → 1 lakebase project → 2 build → 3 bundle deploy →
#   4 bundle run (app deployment) → 5 seed → 6 Genie wire-up → 7 verify + URL.
# Critical phases fail fast; seed/Genie/verify degrade gracefully so a partial failure still leaves
# a working core app.

set -euo pipefail

APP_RESOURCE_KEY="supply_chain_planner"          # stable DABs resource key (NOT the deployed name)
SEED_JOB_KEY="setup_and_seed"
MIN_CLI_VERSION="0.295.0"

PROFILE="${DATABRICKS_CONFIG_PROFILE:-}"
TARGET="dev"
SEED=true
VERIFY=true
APP_ONLY=false
BUILD_FRONTEND=""                                 # "" = decide by mode after parsing
GENIE_CONSUMER_GROUP=""
EXTRA_VARS=()

# ── Args ──────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile|-p)            PROFILE="$2"; shift 2 ;;
    --target|-t)             TARGET="$2"; shift 2 ;;
    --seed)                  SEED=true; shift ;;
    --no-seed)               SEED=false; shift ;;
    --no-verify)             VERIFY=false; shift ;;
    --app-only)              APP_ONLY=true; shift ;;
    --build-frontend)        BUILD_FRONTEND=true; shift ;;
    --no-build-frontend)     BUILD_FRONTEND=false; shift ;;
    --genie-consumer-group)  GENIE_CONSUMER_GROUP="$2"; shift 2 ;;
    --var)                   EXTRA_VARS+=(--var "$2"); shift 2 ;;
    -h|--help)               grep '^#' "$0" | grep -v '^#!' | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $1 (try --help)" >&2; exit 1 ;;
  esac
done

# Build by default on a full deploy; only when asked on --app-only.
[[ -z "$BUILD_FRONTEND" ]] && { $APP_ONLY && BUILD_FRONTEND=false || BUILD_FRONTEND=true; }

# Run from the repo root (this script lives in scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

info() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

[[ -n "$PROFILE" ]] || die "set --profile <cli-profile> (or export DATABRICKS_CONFIG_PROFILE)"
export DATABRICKS_CONFIG_PROFILE="$PROFILE"       # picked up by the CLI and by uv-run python scripts
PROFILE_FLAG=(--profile "$PROFILE")
TARGET_FLAG=(-t "$TARGET")

DEGRADED=()                                       # non-fatal steps that were skipped/failed

# ── Cached `bundle validate --output json` → small python helpers ───────────────
BUNDLE_JSON=""
bundle_json() {
  if [[ -z "$BUNDLE_JSON" ]]; then
    BUNDLE_JSON=$(databricks bundle validate "${PROFILE_FLAG[@]}" "${TARGET_FLAG[@]}" ${EXTRA_VARS[@]+"${EXTRA_VARS[@]}"} \
      --output json 2>/dev/null || echo '{}')
  fi
  printf '%s' "$BUNDLE_JSON"
}
bundle_var() {
  bundle_json | python3 -c "
import json,sys
d=json.load(sys.stdin); v=d.get('variables',{}).get('$1',{})
print(v.get('value', v.get('default','')) if isinstance(v,dict) else v)" 2>/dev/null || true
}
bundle_app_name() {
  bundle_json | python3 -c "
import json,sys
d=json.load(sys.stdin)
for a in d.get('resources',{}).get('apps',{}).values(): print(a.get('name','')); break" 2>/dev/null | head -1 || true
}

# ── Phase 0: preflight (full deploy only) ───────────────────────────────────────
preflight() {
  info "Preflight (cold-start checks)"

  command -v databricks >/dev/null 2>&1 || die "Databricks CLI not found. Install it: https://docs.databricks.com/dev-tools/cli"
  local ver
  ver=$(databricks -v 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  if [[ -z "$ver" ]]; then
    warn "Could not parse Databricks CLI version; need ≥ $MIN_CLI_VERSION for the autoscaling postgres resource."
  elif python3 -c "import sys; v=tuple(map(int,'$ver'.split('.'))); m=tuple(map(int,'$MIN_CLI_VERSION'.split('.'))); sys.exit(0 if v<m else 1)"; then
    die "Databricks CLI $ver is too old — need ≥ $MIN_CLI_VERSION (autoscaling \`postgres\` app resource). Upgrade and retry."
  else
    ok "Databricks CLI $ver (≥ $MIN_CLI_VERSION)"
  fi

  local me
  me=$(databricks current-user me "${PROFILE_FLAG[@]}" --output json 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('userName',''))" 2>/dev/null || true)
  [[ -n "$me" ]] || die "Profile '$PROFILE' is not authenticated. Run: databricks auth login --profile $PROFILE"
  ok "Authenticated as $me"

  command -v uv >/dev/null 2>&1 || die "uv not found (needed to run setup scripts). Install: https://docs.astral.sh/uv/"
  if $BUILD_FRONTEND; then
    command -v npm >/dev/null 2>&1 || die "npm/node not found (needed to build the SPA). Install Node 18+."
    ok "uv + node present"
  else
    ok "uv present"
  fi

  local cat; cat=$(bundle_var uc_catalog)
  if [[ -n "$cat" ]]; then
    if databricks catalogs get "$cat" "${PROFILE_FLAG[@]}" >/dev/null 2>&1; then
      ok "UC catalog '$cat' exists"
    else
      warn "UC catalog '$cat' not found (or no access). Create it / pass --var uc_catalog=<your-catalog>; the seed writes there."
    fi
  fi
  warn "Seed runs on serverless compute — ensure serverless is enabled in this workspace (data-gen tasks need it)."

  # The two Genie+OBO steps a deploy CANNOT do (security-gated) — surface them up front.
  cat <<'BANNER'

  ── Genie via OBO needs two manual, one-time steps this script cannot automate ──
   [ ] A workspace admin enables the "Databricks Apps – On-Behalf-Of-User Authorization" Public Preview.
   [ ] Each end user accepts the OAuth consent on first open (a stale browser session → 403 "invalid scope";
       re-open in a fresh/incognito session). End users also need: CAN_RUN on the Genie space (pass
       --genie-consumer-group <group> to grant it), CAN USE on a serverless/pro warehouse, and SELECT on
       the underlying UC tables. Until then the app still works on every non-Genie route.
BANNER
}

# ── Phase helpers ───────────────────────────────────────────────────────────────
ensure_lakebase_project() {
  info "Ensure Lakebase autoscaling project (idempotent)"
  LAKEBASE_PROJECT="$(bundle_var lakebase_project)" uv run python scripts/ensure_lakebase_project.py \
    || die "Lakebase project provisioning failed (need CAN MANAGE to create/attach). See scripts/ensure_lakebase_project.py output above."
  ok "Lakebase project ready"
}

build_spa() {
  info "Build the React SPA → frontend/dist"
  npm --prefix frontend ci
  npm --prefix frontend run build
  ok "SPA built"
}

bundle_deploy() {
  info "bundle deploy (uploads source + reconciles resources; postgres resource → SP CONNECT+CREATE)"
  databricks bundle deploy "${TARGET_FLAG[@]}" "${PROFILE_FLAG[@]}" ${EXTRA_VARS[@]+"${EXTRA_VARS[@]}"} || die "bundle deploy failed"
  BUNDLE_JSON=""   # config may have changed; refetch on next read
  ok "bundle deployed"
}

bundle_run_app() {
  info "bundle run $APP_RESOURCE_KEY (creates the active deployment — bundle deploy only made the shell)"
  databricks bundle run "$APP_RESOURCE_KEY" "${TARGET_FLAG[@]}" "${PROFILE_FLAG[@]}" ${EXTRA_VARS[@]+"${EXTRA_VARS[@]}"} \
    || die "bundle run $APP_RESOURCE_KEY failed (the app shell exists but has no active deployment)"
  ok "app deployment live"
}

seed_demo_data() {
  info "Seed demo data (operational + pgvector + Genie tables + Knowledge/VS)"
  if databricks bundle run "$SEED_JOB_KEY" "${TARGET_FLAG[@]}" "${PROFILE_FLAG[@]}" ${EXTRA_VARS[@]+"${EXTRA_VARS[@]}"}; then
    ok "seed job complete"
  else
    warn "Seed job failed (or partially). The core app still runs; re-run: make seed PROFILE=$PROFILE TARGET=$TARGET"
    DEGRADED+=("seed")
  fi
}

# Patch the genie_space_id var default in databricks.yml so it's durable across full AND quick
# redeploys (quick `make redeploy` doesn't re-run this phase, so the id must live in the bundle).
patch_genie_space_id() {
  python3 - "$1" <<'PY'
import re, sys
space_id = sys.argv[1]
path = "databricks.yml"
lines = open(path).readlines()
in_block = done = False
out = []
for line in lines:
    if re.match(r'^  genie_space_id:\s*$', line):
        in_block = True
    elif in_block and re.match(r'^  [A-Za-z0-9_]', line):
        in_block = False
    if in_block and not done and re.match(r'^    default:\s*', line):
        line = f'    default: "{space_id}"\n'
        done = True
    out.append(line)
open(path, "w").writelines(out)
print("patched" if done else "no-change")
PY
}

genie_wire_up() {
  local existing; existing="$(bundle_var genie_space_id)"
  if [[ -n "$existing" && "$existing" != "unset" ]]; then
    info "Genie already wired (genie_space_id=$existing) — skipping bootstrap"
    return
  fi
  info "Genie wire-up (create/find space → grant → capture id → redeploy)"
  local gcg; gcg="${GENIE_CONSUMER_GROUP:-$(bundle_var genie_consumer_group)}"
  local out sid
  out=$(GENIE_CONSUMER_GROUP="$gcg" uv run python data/genie/02_create_genie_space.py 2>&1) || true
  printf '%s\n' "$out" | sed 's/^/    /'
  sid=$(printf '%s\n' "$out" | grep -oE 'GENIE_SPACE_ID=[0-9a-fA-F-]+' | head -1 | cut -d= -f2)
  if [[ -z "$sid" ]]; then
    warn "Could not create/capture the Genie space — Analytics route stays off (app works on every other route)."
    DEGRADED+=("genie")
    return
  fi
  if [[ "$(patch_genie_space_id "$sid")" == "patched" ]]; then
    ok "Wired genie_space_id=$sid into databricks.yml (durable across redeploys)"
  else
    warn "Captured genie_space_id=$sid but could not patch databricks.yml; pass --var genie_space_id=$sid on future deploys."
  fi
  info "Redeploy to apply genie_space_id"
  databricks bundle deploy "${TARGET_FLAG[@]}" "${PROFILE_FLAG[@]}" ${EXTRA_VARS[@]+"${EXTRA_VARS[@]}"} \
    && databricks bundle run "$APP_RESOURCE_KEY" "${TARGET_FLAG[@]}" "${PROFILE_FLAG[@]}" ${EXTRA_VARS[@]+"${EXTRA_VARS[@]}"} \
    || { warn "Redeploy after Genie wiring failed; the id is saved — re-run make deploy."; DEGRADED+=("genie-redeploy"); }
  BUNDLE_JSON=""
}

verify_and_report() {
  local app_name; app_name="$(bundle_app_name)"

  if $VERIFY && [[ -n "$app_name" ]]; then
    info "Verify deployed state (Lakebase + operational + write-back + SP grant)"
    if APP_NAME="$app_name" uv run python scripts/verify_deploy.py; then
      ok "verify_deploy passed"
    else
      warn "verify_deploy reported failures (see above) — app may still be usable; investigate the FAIL lines."
      DEGRADED+=("verify")
    fi
  fi

  if [[ -n "$app_name" ]]; then
    info "Wait for app compute to become ACTIVE"
    local state url i
    for ((i=0; i<36; i++)); do
      state=$(databricks apps get "$app_name" "${PROFILE_FLAG[@]}" --output json 2>/dev/null \
        | python3 -c "import json,sys; print(json.load(sys.stdin).get('compute_status',{}).get('state',''))" 2>/dev/null || true)
      [[ "$state" == "ACTIVE" ]] && break
      sleep 10
    done
    [[ "$state" == "ACTIVE" ]] && ok "app compute ACTIVE" || warn "app compute state: ${state:-unknown} (give it a moment, then check the UI)"
    url=$(databricks apps get "$app_name" "${PROFILE_FLAG[@]}" --output json 2>/dev/null \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('url',''))" 2>/dev/null || true)
    [[ -n "$url" ]] && printf '\n\033[1;32m  App URL: %s/ui\033[0m\n' "$url"
  fi

  printf '\n\033[1;36m▶ Done.\033[0m App=%s  target=%s\n' "${app_name:-?}" "$TARGET"
  if [[ ${#DEGRADED[@]} -gt 0 ]]; then
    warn "Degraded (non-fatal) steps: ${DEGRADED[*]} — the core app deployed; see warnings above to finish wiring."
  fi
}

# ── Orchestrate ─────────────────────────────────────────────────────────────────
if $APP_ONLY; then
  info "Quick deploy (--app-only): build(opt) → bundle deploy → bundle run"
  if $BUILD_FRONTEND; then build_spa; fi
  bundle_deploy
  bundle_run_app
  verify_and_report
  exit 0
fi

preflight
ensure_lakebase_project
if $BUILD_FRONTEND; then build_spa; fi
bundle_deploy
bundle_run_app
if $SEED; then
  seed_demo_data
  genie_wire_up
else
  info "Skipping seed (--no-seed) — point uc_catalog/uc_schema at your own data"
fi
verify_and_report
