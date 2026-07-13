# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — The Agent, End to End: Routing, Tracing, and Human-in-the-Loop
# MAGIC
# MAGIC Notebooks 01-02 called the three retrieval engines by hand. In production, a supervisor
# MAGIC decides which ones to call. This notebook builds the real LangGraph agent, tests a few
# MAGIC requests against it, watches an MLflow trace render itself inline, and drives the "hero loop"
# MAGIC — a recommendation that needs human approval before anything gets written.
# MAGIC
# MAGIC **Run this in the actual Databricks workspace notebook UI** — the inline MLflow trace widget
# MAGIC only renders in the notebook front-end, not via `python file.py`. The mechanics below still
# MAGIC work locally (Databricks Connect); you just won't see the trace panel.
# MAGIC
# MAGIC **This notebook performs real writes** — but to an *isolated* memory schema (see the widget
# MAGIC below), never the live app's own memory. Safe to re-run.

# COMMAND ----------
# MAGIC %md
# MAGIC ### Configure this notebook
# MAGIC `LAKEBASE_AUTOSCALING_PROJECT`/`BRANCH` map to `agent_server.config.settings` (leave blank if
# MAGIC already set on your cluster or `.env`). `user_id` and `memory_schema_override` are
# MAGIC **notebook-local only** — they never touch the shared settings object, which is what keeps
# MAGIC this tour's writes isolated from the live app's memory. Change a value, then
# MAGIC **Run ▸ Clear State and Run All**.

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
    dbutils.widgets.text("user_id", "notebook-tour@databricks.com", "Your user id")
    dbutils.widgets.text("memory_schema_override", "notebook_tour_memory", "Isolated memory schema")
    user_id = dbutils.widgets.get("user_id")
    memory_schema_override = dbutils.widgets.get("memory_schema_override")
except Exception:
    user_id = "notebook-tour@databricks.com"
    memory_schema_override = "notebook_tour_memory"

print(f"user_id                 : {user_id}")
print(f"isolated memory schema  : {memory_schema_override}")

# COMMAND ----------
import dataclasses
import os
import uuid

import mlflow
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agent_server.config import settings  # picks up the widgets above
from agent_server.contracts import HITLDecision, HITLVerdict
from agent_server.graph.build_graph import build_graph
from agent_server.lakebase import init_lakebase_config, lakebase_context

# Call directly rather than importing agent_server.agent, which also wires FastAPI handlers.
mlflow.langchain.autolog()
print(f"UC catalog/schema: {settings.uc_catalog}.{settings.uc_schema} (same tables as notebooks 01-02)")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 0 — test requests offline, no cost
# MAGIC
# MAGIC Sanity-check the router before touching live infrastructure. The router itself is a real LLM
# MAGIC call; only the three gather engines are stubbed (`USE_STUBS=1`), and state lives in an
# MAGIC in-memory checkpointer — nothing persists.

# COMMAND ----------
os.environ["USE_STUBS"] = "1"
offline_graph = build_graph(checkpointer=MemorySaver())

ROUTING_QUESTIONS = [
    "What is the total open PO quantity by supplier for Q4 2026?",
    "What do our Caterpillar contracts say about late-delivery penalties?",
    "Find similar past quality incidents to Henkel's SKU-1001 adhesive cracking.",
]

for i, question in enumerate(ROUTING_QUESTIONS):
    cfg = {"configurable": {"thread_id": f"routing-check-{i}", "user_id": user_id}}
    result = await offline_graph.ainvoke({"question": question, "user_id": user_id}, cfg)
    print(f"{question!r}\n  -> {result['route_decision'].agents}  ({result['route_decision'].reasoning})\n")

os.environ["USE_STUBS"] = "0"

# COMMAND ----------
# MAGIC %md
# MAGIC ## Step 1 — for real, against Lakebase
# MAGIC
# MAGIC Now open the same Postgres backend the deployed app uses, but in the isolated schema from the
# MAGIC widget above — this tour never touches the live app's memory.

# COMMAND ----------
cfg = dataclasses.replace(init_lakebase_config(), memory_schema=memory_schema_override)
print(f"Lakebase target: {cfg.description}  |  memory schema: {cfg.memory_schema}")

# Entered manually (not `async with`) because the connection needs to stay open across the many
# cells below, not just one `with` block — closed explicitly in the cleanup cell at the end.
lb_cm = lakebase_context(cfg)
checkpointer, store = await lb_cm.__aenter__()
await checkpointer.setup()
await store.setup()
graph = build_graph(checkpointer=checkpointer)
print("Checkpointer + store ready.")

# COMMAND ----------
# MAGIC %md
# MAGIC A small turn-driver, structurally mirroring `_drive` in
# MAGIC `agent_server/evaluation/memory_validation.py`. **Critically**, `config["configurable"]` must
# MAGIC include `"store": store` alongside `thread_id`/`user_id` — `hydrate_memory_node` and
# MAGIC `commit_node` read the store from there, not from a graph-level binding. Omit it and memory
# MAGIC silently no-ops (an empty `MemoryContext`, never an error).

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

# COMMAND ----------
# MAGIC %md
# MAGIC ## Turn 1 — the hero loop: ask, pause for approval, approve
# MAGIC
# MAGIC The pause is real — a Lakebase checkpoint, not a crash. Watch the MLflow trace panel render
# MAGIC below this cell.

# COMMAND ----------
Q_ACTION = (
    "Henkel's structural adhesive SKU-1001 has recurring quality issues — show me the "
    "similar past cases joined to on-hand inventory and open POs, and recommend a mitigation."
)

thread_a = uuid.uuid4().hex
state1, interrupt1 = await ask(Q_ACTION, thread_a)

if interrupt1:
    print("── HITL interrupt (approval card) ──")
    print(f"Recommendation: {interrupt1['recommendation']['summary']}")
    print(f"Needs approval: {interrupt1['recommendation']['needs_approval']}")

    state1, _ = await ask(
        Q_ACTION, thread_a, resume_verdict="approved", note="Approved via notebook tour.",
    )

print("\n── after commit ──")
print(f"Recommendation summary: {state1['recommendation'].summary}")
print(f"Commit ledger: {state1.get('commit_ledger')}")

# COMMAND ----------
# MAGIC %md
# MAGIC `snap.next` shows the graph reconstructed its paused position from the Lakebase checkpoint —
# MAGIC not from a Python variable still sitting in this notebook's memory.

# COMMAND ----------
snap = await graph.aget_state({"configurable": {"thread_id": thread_a, "user_id": user_id, "store": store}})
print(f"Paused at (should be empty now that we resumed): {snap.next}")
print("Trace notes:")
for note in state1.get("trace_notes", []):
    print(f"  • {note}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## Short-term state — same thread, follow-up
# MAGIC
# MAGIC Conversational history is threaded to the planner via the checkpoint, not held in this
# MAGIC notebook's Python state.

# COMMAND ----------
Q_SHORTTERM2 = "And what is the current on-hand quantity for that same SKU?"

state2, _ = await ask(Q_SHORTTERM2, thread_a)
print(f"Messages carried forward on this thread: {len(state2['messages'])}")
print(f"Answer context: {state2['recommendation'].summary if state2.get('recommendation') else state2.get('operational_result')}")

# COMMAND ----------
# MAGIC %md
# MAGIC A long-term memory of this approval also got written — to the isolated schema, not the live
# MAGIC app's. The next notebook proves it survives outside this one, in a fresh kernel.
# MAGIC
# MAGIC ### Clean up
# MAGIC Close the connection this notebook opened manually above.

# COMMAND ----------
await lb_cm.__aexit__(None, None, None)
print("Lakebase connection closed.")

# COMMAND ----------
# MAGIC %md
# MAGIC Next up: **`04_long_term_memory.py`**.
