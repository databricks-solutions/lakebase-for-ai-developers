"""Agent server entry point.

`LongRunningAgentServer` (databricks-ai-bridge) provides the FastAPI app, the run/poll/resume
background-task transport, and the chat proxy — the in-process "run the graph as background work,
UI polls state" model from CLAUDE.md. The lifespan opens the Lakebase checkpointer + store once
and reuses them across requests (see `agent_server.lakebase`).

`.env` is loaded before agent imports so local auth/config resolves (no-op on Databricks, where
`config.settings` skips dotenv). On Databricks the app/service-principal supplies credentials.
"""

# ruff: noqa: E402
import logging
import os
from contextlib import asynccontextmanager

if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):  # local only
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=True)

from databricks_ai_bridge.long_running import LongRunningAgentServer

# Importing the agent registers the @invoke/@stream handlers with the server.
import agent_server.agent  # noqa: F401
from agent_server.agent import LAKEBASE_CONFIG
from agent_server.lakebase import (
    get_lakebase_access_error_message,
    lakebase_context,
    set_lakebase_resources,
)

logger = logging.getLogger(__name__)

agent_server = LongRunningAgentServer(
    "ResponsesAgent",
    # Disabled: the chat proxy serves "/" by forwarding to a Next.js app on CHAT_APP_PORT (3000)
    # we don't run — it 503s. Our own React SPA is served at /ui by agent_server.webapp, and "/"
    # redirects there. Re-enable only if reinstating the template's separate chat frontend.
    enable_chat_proxy=False,
    db_instance_name=LAKEBASE_CONFIG.instance_name,
    db_autoscaling_endpoint=LAKEBASE_CONFIG.autoscaling_endpoint,
    db_project=LAKEBASE_CONFIG.autoscaling_project,
    db_branch=LAKEBASE_CONFIG.autoscaling_branch,
    task_timeout_seconds=float(os.getenv("TASK_TIMEOUT_SECONDS", "3600")),
    poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "1.0")),
)

# Module-level `app` so multi-worker servers can import it by string.
app = agent_server.app

_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan(app):
    try:
        async with lakebase_context(LAKEBASE_CONFIG) as (checkpointer, store):
            await checkpointer.setup()
            await store.setup()
            logger.info("Lakebase setup complete (%s)", LAKEBASE_CONFIG.description)

            app.state.checkpointer = checkpointer
            app.state.store = store
            set_lakebase_resources(checkpointer, store)

            async with _original_lifespan(app):
                yield
    except Exception as exc:
        if any(
            k in str(exc).lower()
            for k in ["lakebase", "pg_hba", "postgres", "database instance", "insufficient privilege"]
        ):
            logger.error(
                "Lakebase session setup failed: %s\n\n%s",
                exc,
                get_lakebase_access_error_message(LAKEBASE_CONFIG.description),
            )
        else:
            logger.error("Lakebase session setup failed: %s", exc, exc_info=True)
        raise


app.router.lifespan_context = _lifespan

# Register the custom web UI (chat SPA + /api/me + /api/sessions + /api/explorer) onto `app`.
# Imported last so `app` is fully defined first (webapp.py does `from ...start_server import app`).
# NOTE: `from agent_server import webapp`, NOT `import agent_server.webapp` — the latter rebinds
# the name `agent_server` to the package, shadowing the LongRunningAgentServer instance below and
# breaking `agent_server.run(...)` in main().
from agent_server import webapp  # noqa: E402,F401


def main():
    agent_server.run(app_import_string="agent_server.start_server:app")


if __name__ == "__main__":
    main()
