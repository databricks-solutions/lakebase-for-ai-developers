# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — Long-Term Memory: What the Agent Remembers Across Sessions
# MAGIC
# MAGIC Notebook 03's Python variables are gone. This is a fresh kernel, a fresh thread — does the
# MAGIC agent still remember the approval you just made?
# MAGIC
# MAGIC **Recommend actually restarting this notebook's Python state** (Run ▸ Clear State, or
# MAGIC Detach & Re-attach) before running it, even if you're continuing straight from notebook 03 —
# MAGIC that's what makes the recall below a genuine cross-session proof, not just a lingering
# MAGIC variable.
# MAGIC
# MAGIC **Requires notebook 03 to have run at least once, approved, with the same `user_id`.**

# COMMAND ----------
# MAGIC %md
# MAGIC ### Configure this notebook
# MAGIC These must match what you used in notebook 03 (same defaults, so this "just works" unless you
# MAGIC customized notebook 03's widgets — in which case, enter the same values here).

# COMMAND ----------
import sys
from pathlib import Path

try:
    REPO_ROOT = str(Path(__file__).resolve().parents[1])
except NameError:
    REPO_ROOT = str(Path.cwd().resolve())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    from databricks.sdk.runtime import dbutils
    dbutils.widgets.text("LAKEBASE_AUTOSCALING_PROJECT", "", "Lakebase project (optional)")
    dbutils.widgets.text("LAKEBASE_AUTOSCALING_BRANCH", "", "Lakebase branch (optional)")
    dbutils.widgets.text("user_id", "notebook-tour@databricks.com", "Your user id (must match notebook 03)")
    dbutils.widgets.text("memory_schema_override", "notebook_tour_memory", "Isolated memory schema (must match notebook 03)")
    user_id = dbutils.widgets.get("user_id")
    memory_schema_override = dbutils.widgets.get("memory_schema_override")
except Exception:
    user_id = "notebook-tour@databricks.com"
    memory_schema_override = "notebook_tour_memory"

print(f"user_id                 : {user_id}")
print(f"isolated memory schema  : {memory_schema_override}")

# COMMAND ----------
import dataclasses
import uuid

import mlflow
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agent_server.config import settings  # picks up the widgets above
from agent_server.contracts import HITLDecision, HITLVerdict
from agent_server.graph.build_graph import build_graph
from agent_server.lakebase import init_lakebase_config, lakebase_context

mlflow.langchain.autolog()
print(f"UC catalog/schema: {settings.uc_catalog}.{settings.uc_schema} (same tables as notebooks 01-02)")

cfg = dataclasses.replace(init_lakebase_config(), memory_schema=memory_schema_override)
lb_cm = lakebase_context(cfg)  # entered manually — see notebook 03 for why
checkpointer, store = await lb_cm.__aenter__()
graph = build_graph(checkpointer=checkpointer)
print(f"Reconnected to {cfg.description}  |  memory schema: {cfg.memory_schema}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## A brand-new thread, same user
# MAGIC
# MAGIC `thread_id` below has never been seen by this graph. Memory namespaces key on
# MAGIC `(type, user_id)` (`agent_server/memory.py`) — not on thread — so recall only needs the same
# MAGIC `user_id` as notebook 03, not the same conversation.

# COMMAND ----------
async def ask(question: str, thread_id: str, resume_verdict: str | None = None, note: str | None = None):
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id, "store": store}}
    turn_input = {"question": question, "user_id": user_id, "messages": [HumanMessage(content=question)]}

    interrupt_payload = None
    async for chunk in graph.astream(turn_input, config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            interrupt_payload = chunk["__interrupt__"][0].value

    if interrupt_payload is not None and resume_verdict is not None:
        resume = Command(resume=HITLDecision(
            verdict=HITLVerdict(resume_verdict), note=note, user_id=user_id,
        ).model_dump())
        async for chunk in graph.astream(resume, config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                interrupt_payload = chunk["__interrupt__"][0].value

    snap = await graph.aget_state(config)
    return dict(snap.values) if snap else {}, interrupt_payload


Q_FOLLOWUP = (
    "We had recurring Henkel SKU-1001 adhesive cracking and approved a mitigation before — "
    "what did we decide, and how should we proceed now?"
)

thread_b = uuid.uuid4().hex
state, interrupt = await ask(Q_FOLLOWUP, thread_b)
if interrupt:
    state, _ = await ask(Q_FOLLOWUP, thread_b, resume_verdict="approved", note="Approved via notebook tour (capstone).")

# COMMAND ----------
ctx = state.get("memory_context")
if ctx and not ctx.is_empty:
    print("── memory recalled on a brand-new thread ──")
    for item in ctx.prior_approvals:
        print(f"  [approval]   {item.text}")
    for item in ctx.preferences:
        print(f"  [preference] {item.text}")
    for item in ctx.supplier_notes:
        print(f"  [supplier]   {item.text}")
else:
    print("No memory recalled — did notebook 03 run and approve, with the same user_id?")

print("\nTrace notes:")
for note in state.get("trace_notes", []):
    print(f"  • {note}")

# COMMAND ----------
# MAGIC %md
# MAGIC `hydrate_memory` ran *before* the planner and searched Postgres by embedding similarity over
# MAGIC the stored memory text; the `hydrate_memory →` trace note above is proof it was actually
# MAGIC used, not just present.
# MAGIC
# MAGIC ## See the actual rows
# MAGIC
# MAGIC `user_id` gets sanitized (dots → dashes) before it becomes a Postgres key prefix — that's
# MAGIC `agent_server/memory.py::_sanitize_user`, not a typo below.

# COMMAND ----------
from data.operational._lakebase import connect

sanitized_user = user_id.replace(".", "-")
with connect() as conn, conn.cursor() as cur:
    cur.execute(
        f"SELECT prefix, key, value, updated_at FROM {memory_schema_override}.store "
        "ORDER BY updated_at DESC LIMIT 20"
    )
    rows = cur.fetchall()

print(f"Most recent rows in {memory_schema_override}.store (look for prefixes starting "
      f"'approvals.{sanitized_user}' / 'preferences.{sanitized_user}'):\n")
for row in rows:
    print(f"  {row}")

# COMMAND ----------
# MAGIC %md
# MAGIC A pause that survived a kernel restart (notebook 03), and a decision recalled on a brand-new
# MAGIC thread in a brand-new kernel (this notebook): one Postgres project, not Redis + DynamoDB + a
# MAGIC standalone vector DB.
# MAGIC
# MAGIC ### Clean up

# COMMAND ----------
await lb_cm.__aexit__(None, None, None)
print("Lakebase connection closed.")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Where to go from here
# MAGIC
# MAGIC - [`docs/architecture.md`](../docs/architecture.md) — the full technical writeup
# MAGIC - [`docs/storyboard.md`](../docs/storyboard.md) — the sales narrative this tour is based on
# MAGIC - The repo root [`README.md`](../README.md) "Try it" section — the polished 2-minute version
# MAGIC   of everything you just walked through in code
