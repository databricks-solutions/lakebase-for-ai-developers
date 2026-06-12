"""On-behalf-of-user (OBO) token plumbing for Databricks Apps.

Minimal-permission principle: gather tools that read governed surfaces (Vector Search, Genie)
should run AS THE CALLING USER, not the app service principal — so Unity Catalog enforces each
user's own access and the app SP needs no standing grants on those surfaces.

Databricks Apps forward the caller's OAuth token as the `X-Forwarded-Access-Token` header on
every request. The ASGI middleware in `webapp.py` captures it per-request into the contextvar
here; tools read it via `obo_workspace_client()` / `get_obo_token()`. The contextvar is copied
into LangGraph's node executor, so both async and sync gather nodes see the right token.

Falls back to the app SP / ambient auth when no token is present (local dev, eval, or a
background task without a forwarded request) so those paths keep working.
"""

from __future__ import annotations

import contextvars
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_obo_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "obo_access_token", default=None
)


def set_obo_token(token: Optional[str]) -> None:
    _obo_token.set(token or None)


def get_obo_token() -> Optional[str]:
    try:
        return _obo_token.get()
    except LookupError:
        return None


def workspace_host() -> Optional[str]:
    """Workspace base URL with a scheme (Apps' DATABRICKS_HOST is often a bare hostname)."""
    host = os.environ.get("DATABRICKS_HOST") or ""
    if host and not host.startswith(("http://", "https://")):
        host = "https://" + host
    if not host:
        try:
            from databricks.sdk import WorkspaceClient

            host = WorkspaceClient().config.host
        except Exception:  # noqa: BLE001
            host = ""
    return host or None


def obo_workspace_client():
    """A WorkspaceClient acting as the calling user when a forwarded token is present; returns
    None otherwise so the caller falls back to the app SP (`WorkspaceClient()`)."""
    token = get_obo_token()
    if not token:
        return None
    try:
        from databricks.sdk import WorkspaceClient

        # auth_type="pat" is load-bearing on Databricks Apps: the runtime injects the app SP's
        # DATABRICKS_CLIENT_ID/SECRET (auth group "oauth") into the env, so passing token= (group
        # "pat") leaves the SDK with two configured auth methods and Config._validate() raises
        # "more than one authorization method configured". Pinning auth_type short-circuits that
        # validation and forces the user's PAT — without it the OBO client construction throws,
        # we silently fall back to the app SP, and Genie/UC reads run as the SP (PERMISSION_DENIED).
        host = workspace_host()
        return (
            WorkspaceClient(host=host, token=token, auth_type="pat")
            if host
            else WorkspaceClient(token=token, auth_type="pat")
        )
    except Exception as exc:  # noqa: BLE001 — log, don't mask: a silent fallback to the app SP here
        # is exactly what hid this bug. Surface it so an auth misconfig is visible in app logs.
        logger.warning("OBO WorkspaceClient construction failed; falling back to app SP: %s", exc)
        return None
