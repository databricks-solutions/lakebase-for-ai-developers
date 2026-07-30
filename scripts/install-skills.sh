#!/usr/bin/env bash
# Install the externally-managed coding-agent skill sets at PINNED versions.
#
# The repo-local agent-langgraph-advanced build skills already live in `.agents/skills/` for Codex
# and `.claude/skills/` for Claude Code (committed).
# This script installs the two skill sets we reference rather than vendor:
#   1) Databricks Agent Skills  (requires Databricks CLI >= v1.0.0)
#   2) MLflow Skills            (tracing + evaluation)
#
# Pin versions for reproducibility. Record the exact CLI version the team standardizes on.
set -euo pipefail

# --- Pins (edit to the versions the team standardizes on) -----------------------------------
DATABRICKS_CLI_MIN_VERSION="1.0.0"   # `databricks aitools` requires >= 1.0.0
# For the plugin-marketplace path, pin to a commit SHA instead of a moving branch:
# DATABRICKS_AGENT_SKILLS_REF="<commit-sha>"
# --------------------------------------------------------------------------------------------

echo "==> Databricks CLI version"
if ! command -v databricks >/dev/null 2>&1; then
  echo "    Databricks CLI not found. Install >= v${DATABRICKS_CLI_MIN_VERSION} first." >&2
  exit 1
fi
databricks --version || true
echo "    (require >= v${DATABRICKS_CLI_MIN_VERSION})"

echo "==> Installing stable Databricks Agent Skills (auto-detects coding agent)"
# Canonical path:
databricks aitools install
# Targeted / experimental examples:
#   databricks aitools install databricks-lakebase
#   databricks aitools install databricks-vector-search
#   databricks aitools install databricks-mlflow-evaluation --experimental
#
# Plugin-marketplace alternative (Claude Code) — pin to a commit SHA:
#   /plugin marketplace add databricks/databricks-agent-skills
#   /plugin install databricks@databricks-agent-skills

echo "==> Installing MLflow Skills (tracing + evaluation)"
npx skills add mlflow/skills

echo "==> Done. See .agents/skills/README.md, .claude/skills/README.md, and docs/references.md."
