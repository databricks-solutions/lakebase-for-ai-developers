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
	databricks bundle validate -t $(TARGET) --profile $(PROFILE)

# Build + deploy the App/experiment, then seed demo data unless SEED=false.
deploy: _require_profile build
	databricks bundle deploy -t $(TARGET) --profile $(PROFILE)
	@if [ "$(SEED)" = "true" ]; then \
	  echo "==> seeding demo data (SEED=true)"; \
	  $(MAKE) seed PROFILE=$(PROFILE) TARGET=$(TARGET); \
	else \
	  echo "==> skipping seed (SEED=false) — point uc_catalog/uc_schema at your own data"; \
	fi
	@echo "==> done. After the seed finishes, grab the Genie space id from the create_genie_space"
	@echo "    task output, set var.genie_space_id, and re-run 'make deploy' to wire Analytics."

# Load the demo dataset (operational + pgvector + Genie + Knowledge/Vector-Search).
seed: _require_profile
	databricks bundle run setup_and_seed -t $(TARGET) --profile $(PROFILE)

destroy: _require_profile
	databricks bundle destroy -t $(TARGET) --profile $(PROFILE)

.PHONY: _require_profile build validate deploy seed destroy
