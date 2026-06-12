"""Shared pytest config: gate the integration tests behind a live Lakebase + an explicit opt-in.

Integration tests (`@pytest.mark.integration`, under tests/integration/) talk to a real Lakebase
branch and the agent server, so they are SKIPPED by default. They run only when BOTH:
  * RUN_INTEGRATION=1 is set (explicit opt-in — never run accidentally in CI/offline), AND
  * Lakebase connection coordinates are configured — an autoscaling endpoint/project, an
    explicit LAKEBASE_PG_URL, a provisioned instance, or a CLI profile (DATABRICKS_CONFIG_PROFILE)
    that the SDK credential chain can resolve.

The unit tests (everything else) never import this gate and run fully offline.
"""

from __future__ import annotations

import os

import pytest

# Load .env locally (mirrors agent_server.config) so RUN_INTEGRATION / Lakebase coords set there
# are visible. No-op on Databricks and harmless if python-dotenv is missing.
if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass


def _lakebase_configured() -> bool:
    """True when SOME Lakebase connection form is configured (the integration tests can connect)."""
    return bool(
        os.environ.get("LAKEBASE_AUTOSCALING_ENDPOINT")
        or os.environ.get("LAKEBASE_AUTOSCALING_PROJECT")
        or os.environ.get("LAKEBASE_PG_URL")
        or os.environ.get("LAKEBASE_INSTANCE_NAME")
        or os.environ.get("DATABRICKS_CONFIG_PROFILE")
    )


def _integration_skip_reason() -> str | None:
    """None → run; a string → skip with that reason. Single source of truth for the gate."""
    if os.environ.get("RUN_INTEGRATION", "").strip() not in ("1", "true", "True", "yes"):
        return "integration tests are opt-in: set RUN_INTEGRATION=1 to run"
    if not _lakebase_configured():
        return (
            "no Lakebase connection configured (set LAKEBASE_AUTOSCALING_ENDPOINT/PROJECT, "
            "LAKEBASE_PG_URL, LAKEBASE_INSTANCE_NAME, or DATABRICKS_CONFIG_PROFILE)"
        )
    return None


def pytest_configure(config: pytest.Config) -> None:
    # Register the marker here too (in addition to pyproject) so `-W error` / strict-markers is safe
    # even if pyproject isn't picked up.
    config.addinivalue_line(
        "markers", "integration: requires a live Lakebase (skipped by default; RUN_INTEGRATION=1)"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip every @pytest.mark.integration test unless the gate is open."""
    reason = _integration_skip_reason()
    if reason is None:
        return
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker("integration") is not None:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def require_lakebase() -> None:
    """Opt-in fixture an integration test can also depend on for an explicit, per-test gate."""
    reason = _integration_skip_reason()
    if reason is not None:
        pytest.skip(reason)
