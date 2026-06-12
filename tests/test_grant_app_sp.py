"""Offline tests for the slimmed app-SP grant script (data/operational/05_grant_app_sp.py).

Contract (post-change): the `postgres` app resource in databricks.yml now registers the SP as a
Postgres role AND grants it CAN_CONNECT_AND_CREATE on the database — so the seed-time grant script
no longer needs to do either. It builds ONLY the three operational-schema SELECT grants:
  - GRANT USAGE ON SCHEMA ...
  - GRANT SELECT ON ALL TABLES IN SCHEMA ...
  - ALTER DEFAULT PRIVILEGES IN SCHEMA ... GRANT SELECT ON TABLES ...
and has NO role-registration (`_ensure_role` / POST /api/2.0/postgres/.../roles) and NO
`GRANT CREATE ON DATABASE`.

We inspect the script's *source text* (robust to the parallel implementation not being importable
yet — e.g. it references `settings.app_name`, which may land in config separately) plus, when it
imports cleanly, its module attributes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GRANT_PATH = _REPO_ROOT / "data" / "operational" / "05_grant_app_sp.py"


def _source() -> str:
    if not _GRANT_PATH.exists():
        pytest.skip(f"{_GRANT_PATH} not present yet (parallel impl in flight)")
    return _GRANT_PATH.read_text(encoding="utf-8")


# ── Source-level assertions (always available, no import / no network) ────────────────────────

def test_grant_builds_only_select_grants():
    src = _source()
    assert "GRANT USAGE" in src, "must keep USAGE on the operational schema"
    assert "GRANT SELECT ON ALL TABLES" in src, "must keep SELECT on all operational tables"
    assert "ALTER DEFAULT PRIVILEGES" in src, "must keep default-privilege SELECT for new tables"
    # …and SELECT-only: the default-privileges grant covers TABLES SELECT.
    assert "GRANT SELECT ON TABLES" in src


def test_grant_does_not_grant_create_on_database():
    src = _source()
    assert "CREATE ON DATABASE" not in src, (
        "the `postgres` app resource now grants CAN_CONNECT_AND_CREATE — the seed grant must NOT "
        "GRANT CREATE ON DATABASE"
    )


def test_grant_has_no_role_registration():
    src = _source()
    # No bespoke role-registration helper…
    assert "_ensure_role" not in src, "role registration now belongs to the `postgres` app resource"
    # …and no raw REST role-registration call.
    assert "/roles" not in src, "must not POST to /api/2.0/postgres/.../roles"
    assert "identity_type" not in src and "postgres_role" not in src, (
        "role-spec fields indicate the removed _ensure_role path is back"
    )


def test_grant_keeps_select_grants_count_minimal():
    # Exactly the three operational SELECT grants — no extra write/create grant slipped in.
    # (Check GRANT verbs specifically — a bare "INSERT" would false-match `sys.path.insert`.)
    src = _source().upper()
    assert "GRANT ALL" not in src
    for forbidden in ("GRANT CREATE ON SCHEMA", "GRANT INSERT", "GRANT UPDATE", "GRANT DELETE"):
        assert forbidden not in src, f"unexpected grant: {forbidden}"


# ── Module-attribute assertions (best-effort: only when the script imports cleanly) ───────────

def _load_module():
    """Import 05_grant_app_sp.py by path. Returns the module, or None if it can't import yet
    (the parallel implementation may reference config attrs that aren't landed). The numeric-prefix
    filename isn't a valid identifier, so importlib-from-path is the only way in."""
    if not _GRANT_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location("_grant_app_sp_under_test", _GRANT_PATH)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001 — config/SDK attr not landed yet; source-level tests still cover it
        return None
    return mod


def test_module_has_no_ensure_role_attribute():
    mod = _load_module()
    if mod is None:
        pytest.skip("grant script not importable yet; covered by source-level tests above")
    assert not hasattr(mod, "_ensure_role"), "the role-registration helper must be gone"
    # The grant entrypoint survives.
    assert hasattr(mod, "main") and callable(mod.main)
