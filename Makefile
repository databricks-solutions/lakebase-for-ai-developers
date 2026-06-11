# One-shot deploy for the Supply-Chain Planner Copilot (DABs).
#
#   make deploy PROFILE=<cli-profile>                  # build SPA, deploy bundle, seed demo data
#   make deploy PROFILE=<cli-profile> SEED=false       # bring your own data (skip the seed job)
#   make deploy PROFILE=<cli-profile> TARGET=demo      # clean prod-style names (default: dev)
#   make build   / make validate / make seed / make destroy
#
# Prereqs (one-time, see docs/DEPLOY.md): a CLI profile pointed at your workspace, a Lakebase
# autoscaling project, a writable UC catalog, Node 18+ for the SPA build.

TARGET ?= dev
SEED   ?= true

# Optional bundle-variable overrides (no need to edit databricks.yml per workspace), e.g.:
#   make deploy PROFILE=<p> VARS="uc_catalog=serverless_stable_96t79b_catalog lakebase_project=my-proj"
VARS ?=
VAR_FLAGS := $(foreach v,$(VARS),--var $(v))

ifndef PROFILE
PROFILE := $(DATABRICKS_CONFIG_PROFILE)
endif

_require_profile:
	@if [ -z "$(PROFILE)" ]; then \
	  echo "ERROR: set PROFILE=<cli-profile> (or export DATABRICKS_CONFIG_PROFILE)"; exit 1; fi

# Build the React SPA into frontend/dist (bundle ships it via sync.include).
build:
	npm --prefix frontend ci
	npm --prefix frontend run build

validate: _require_profile
	databricks bundle validate -t $(TARGET) --profile $(PROFILE) $(VAR_FLAGS)

# Build + deploy the App/experiment, then seed demo data unless SEED=false.
# NOTE: `bundle deploy` only uploads source + creates the app OBJECT (the shell). It does NOT
# create an app DEPLOYMENT — that needs `bundle run <app-key>` (= apps deploy), which points the
# app at the source and makes it live. Without this the app shows "No source code / Unavailable".
deploy: _require_profile build
	databricks bundle deploy -t $(TARGET) --profile $(PROFILE) $(VAR_FLAGS)
	@echo "==> deploying the app (creates the active deployment; bundle deploy only made the shell)"
	databricks bundle run supply_chain_planner -t $(TARGET) --profile $(PROFILE) $(VAR_FLAGS)
	@if [ "$(SEED)" = "true" ]; then \
	  echo "==> seeding demo data (SEED=true)"; \
	  $(MAKE) seed PROFILE=$(PROFILE) TARGET=$(TARGET); \
	else \
	  echo "==> skipping seed (SEED=false) — point uc_catalog/uc_schema at your own data"; \
	fi
	@echo "==> done."
	@if [ "$(SEED)" = "true" ]; then \
	  echo "    NEXT (one-time, to wire the Analytics/Genie route): the seed's create_genie_space task"; \
	  echo "    printed a Genie space id. Grab it (Jobs UI -> create_genie_space task -> Output), then"; \
	  echo "    redeploy the app with it set:"; \
	  echo "      make deploy PROFILE=$(PROFILE) SEED=false VARS=\"$(VARS) genie_space_id=<the-id>\""; \
	  echo "    Until set, every route works except Genie. See docs/DEPLOYMENT_GUIDE.md sec.5."; \
	fi

# Load the demo dataset (operational + pgvector + Genie + Knowledge/Vector-Search).
seed: _require_profile
	databricks bundle run setup_and_seed -t $(TARGET) --profile $(PROFILE)

destroy: _require_profile
	databricks bundle destroy -t $(TARGET) --profile $(PROFILE)

.PHONY: _require_profile build validate deploy seed destroy
