# Deploy for the Supply-Chain Planner Copilot. All deploy logic lives in scripts/deploy.sh
# (idempotent, cold-start-safe, graceful degradation) — these targets are thin wrappers.
#
#   make deploy      PROFILE=<p>                       # full one-shot: build, deploy, seed, verify
#   make deploy      PROFILE=<p> SEED=false            # bring your own data (skip the seed job)
#   make deploy      PROFILE=<p> TARGET=demo           # clean prod-style names (default: dev)
#   make deploy      PROFILE=<p> GENIE_GROUP=<group>   # grant a workspace group CAN_RUN on the Genie space (OBO)
#   make redeploy    PROFILE=<p>                       # FAST dev loop: agent-server code change → deploy + restart
#   make redeploy-ui PROFILE=<p>                       # FAST dev loop: frontend change → build + deploy + restart
#   make build / validate / seed / destroy
#
# Prereqs (one-time, see docs/DEPLOY.md): a CLI profile pointed at your workspace, a writable UC
# catalog, Node 18+ for the SPA build, and the Databricks CLI >= 1.3.0 (resources.genie_spaces + the
# direct deployment engine; also covers the `postgres` app resource + autoscaling Lakebase APIs). The
# Lakebase project, Genie space, and seed are all handled automatically by scripts/deploy.sh — no
# manual prereq steps. (Existing Terraform-engine deploys: migrate once — see docs/DEPLOY.md.)
#
# Optional bundle-variable overrides (no need to edit databricks.yml per workspace), e.g.:
#   make deploy PROFILE=<p> VARS="uc_catalog=my_catalog lakebase_project=my-proj"
#
# Restricted workspace (deployer can't create a SQL warehouse)? Use the `byo` target, which omits the
# bundle-created warehouse and uses an existing one (you need CAN USE on it):
#   make deploy PROFILE=<p> TARGET=byo VARS="sql_warehouse_id=<existing-id>"

TARGET ?= dev
SEED   ?= true
VARS   ?=
GENIE_GROUP ?=

VAR_FLAGS  := $(foreach v,$(VARS),--var $(v))
SEED_FLAG  := $(if $(filter false,$(SEED)),--no-seed,)
GENIE_FLAG := $(if $(GENIE_GROUP),--genie-consumer-group $(GENIE_GROUP),)

ifndef PROFILE
PROFILE := $(DATABRICKS_CONFIG_PROFILE)
endif

_require_profile:
	@if [ -z "$(PROFILE)" ]; then \
	  echo "ERROR: set PROFILE=<cli-profile> (or export DATABRICKS_CONFIG_PROFILE)"; exit 1; fi

# Full one-shot deploy. scripts/deploy.sh runs the cold-start preflight, ensures the Lakebase
# project, builds the SPA + Genie-space JSON, deploys + starts the app (creating the Genie space),
# seeds, and verifies.
deploy: _require_profile
	./scripts/deploy.sh --profile $(PROFILE) --target $(TARGET) $(SEED_FLAG) $(GENIE_FLAG) $(VAR_FLAGS)

# FAST dev loops — push code + restart the app ONLY (no seed/lakebase steps). Stays on the
# same target/app and never deletes it, so the SP and its Lakebase schemas persist. Safe all day.
#   redeploy    → agent-server (Python) change: bundle deploy + bundle run     (~30-60s)
#   redeploy-ui → frontend change: npm build + bundle deploy + bundle run
redeploy: _require_profile
	./scripts/deploy.sh --profile $(PROFILE) --target $(TARGET) --app-only $(VAR_FLAGS)

redeploy-ui: _require_profile
	./scripts/deploy.sh --profile $(PROFILE) --target $(TARGET) --app-only --build-frontend $(VAR_FLAGS)

# Build the React SPA into frontend/dist (the bundle ships it via sync.include).
build:
	npm --prefix frontend ci
	npm --prefix frontend run build

validate: _require_profile
	databricks bundle validate -t $(TARGET) --profile $(PROFILE) $(VAR_FLAGS)

# Load the demo dataset (operational + pgvector + Genie + Knowledge/Vector-Search).
seed: _require_profile
	databricks bundle run setup_and_seed -t $(TARGET) --profile $(PROFILE) $(VAR_FLAGS)

destroy: _require_profile
	databricks bundle destroy -t $(TARGET) --profile $(PROFILE)

# Cold-start E2E test: deploy to a THROWAWAY Lakebase project + UC schema in an isolated git worktree,
# verify, then tear it all down (always safe — see scripts/integration_test.sh / the integration-test
# skill). Needs CATALOG=<existing-writable-catalog>; pass extra flags via ITEST_ARGS, e.g.
#   make integration-test PROFILE=<p> CATALOG=main
#   make integration-test PROFILE=<p> CATALOG=main ITEST_ARGS="--target byo --sql-warehouse-id <id>"
integration-test: _require_profile
	@if [ -z "$(CATALOG)" ]; then echo "ERROR: set CATALOG=<existing-writable-catalog>"; exit 1; fi
	./scripts/integration_test.sh --profile $(PROFILE) --uc-catalog $(CATALOG) $(ITEST_ARGS)

.PHONY: _require_profile deploy redeploy redeploy-ui build validate seed destroy integration-test
