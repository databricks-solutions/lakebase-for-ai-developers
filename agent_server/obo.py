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
import os
from typing import Optional

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

        host = workspace_host()
        return WorkspaceClient(host=host, token=token) if host else WorkspaceClient(token=token)
    except Exception:  # noqa: BLE001
        return None
