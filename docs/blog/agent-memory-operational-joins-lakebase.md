<!--
Draft technical blog post for the Databricks Community (community.databricks.com).
Status: DRAFT for review. See docs/blog/README-publishing.md for the publishing checklist.
Planning brief: ~/.claude/plans/help-me-brainstorm-a-nested-treehouse.md
-->

# Agent Memory Meets Operational Data: Short-Term, Long-Term, and Semantic Joins on One Lakebase

> **TL;DR** — Production agents need three kinds of state: short-term session memory,
> long-term semantic recall, and access to live operational data. Teams usually stitch these
> together from three different systems. This post shows how a stateful multi-agent
> **Supply-Chain Planner Copilot** puts all three on a single **Lakebase** (managed Postgres)
> backend — including the move that makes it click: running semantic similarity as *one
> predicate inside a governed SQL join* against live operational rows. We'll also cover when to
> reach for Lakebase vs. Mosaic AI Vector Search vs. Genie, and how durable human-in-the-loop
> approval falls out of the same design. **Audience:** AI engineers, ML/data scientists, and
> field engineers building agents on Databricks.

---

## The problem: agents have state, and it lives everywhere

Once an agent graduates from a demo to something people actually use, it grows a memory. Three
kinds of it, really:

- **Short-term memory** — the current session: the conversation so far, the intermediate results
  of this run, the checkpoint you'd resume from if the process restarted.
- **Long-term memory** — what the agent should carry across sessions: user preferences, prior
  decisions, learned facts about the entities it works with. This is the memory you want to
  retrieve *semantically* ("what did we decide last time something like this came up?").
- **Operational data** — the live business records the agent reasons over: inventory, orders,
  customers, transactions. Not "memory" exactly, but the agent is useless without it.

The usual answer is a separate system for each: a fast key-value store for session state, a
dedicated vector database for semantic recall, and a relational or NoSQL database for tool and
operational state. Every system you add is another credential set, another failure mode, and
another copy of your access policy to keep in sync — and a fourth problem that's easy to miss:
**your semantic index can't see your operational data.**

That last one bites in exactly the scenario agents are good at. Take a manufacturing planner
looking at a recurring quality issue on a supplier's part:

> *"Acme Corp's SKU-1001 has recurring adhesive cracking — show me similar past cases, joined to
> on-hand inventory and open POs, and recommend a mitigation."*

Answering that means a **semantic** step (find similar past incidents) *and* a **relational**
step (join those incidents to live inventory and open purchase orders). If similarity lives in
a vector index and the operational rows live in a warehouse, you're doing a vector lookup,
pulling IDs back into the app, then round-tripping to the database to join and re-filter. Two
hops, two governance surfaces, and a join that happens in your application code instead of the
database.

![A multi-agent copilot on Databricks Apps — gather, plan, approve, commit — with all state on Lakebase. Left to right: planners send a question to a Supervisor router, which fans out to parallel gather agents (AI Search, Lakebase pgvector, Genie); a Planner drafts a recommendation; a Combine-and-check gate decides whether to auto-approve or route to a human-in-the-loop review (interrupt()); Commit writes the plan. A Lakebase layer underneath holds the short-term checkpointer, the long-term store, and pgvector + operational rows.](../diagrams/Agentic_Apps_Architecture.001.png)

*The whole system, one picture: a LangGraph agent on Databricks Apps, with short-term memory,
long-term memory, and operational rows all on a single Lakebase backend.*

The rest of this post walks the three kinds of state on that one backend, then the payoff
patterns — the decision framework, durable HITL, and observability — that fall out of it.

---

## Agent memory on Lakebase: short-term and long-term

Lakebase is Databricks' managed, autoscaling Postgres. Because it's *just Postgres*, you're not
locked into any one agent framework — any Postgres client, ORM, or memory library that speaks the
wire protocol works, and pgvector gives you semantic search in the same database. This build uses
**LangGraph**, whose `databricks-langchain` integration manages the memory tables, connection
pooling, OAuth credential rotation, and the vector index *for you* — so you wire up two objects and
get both kinds of memory:

```python
from databricks_langchain import AsyncCheckpointSaver, AsyncDatabricksStore

# Short-term: thread/session checkpoints — resumable runs.
async with AsyncCheckpointSaver(
    project=PROJECT, branch=BRANCH, schema=SCHEMA,
) as checkpointer, AsyncDatabricksStore(
    # Long-term + semantic: pgvector-backed memory, embedded via a Databricks endpoint.
    project=PROJECT, branch=BRANCH, schema=SCHEMA,
    embedding_endpoint="databricks-gte-large-en", embedding_dims=1024,
) as store:
    yield checkpointer, store
```
<sub>Adapted from `agent_server/lakebase.py` (`lakebase_context`).</sub>

**Short-term — the session checkpoint.** *The problem it solves:* a long-running agent shouldn't
lose its place. Every step of a run is checkpointed to Lakebase, so if the app restarts mid-run it
*resumes* from the last checkpoint instead of recomputing expensive retrieval calls. The
conversation log lives here too — a recent window feeds the router and planner, so follow-ups like
*"what about that SKU?"* resolve in context. In this build that's LangGraph's
`AsyncCheckpointSaver`, one thread per planning session.

**Long-term + semantic — the memory store.** *The problem it solves:* carrying knowledge across
sessions and recalling it by meaning, not exact match. This is the pgvector path, but you never
touch pgvector directly — point the store at a Databricks embedding endpoint and it manages the
vector tables and serves semantic search over stored memory. The copilot namespaces memory by
`(planner, entity)` — user preferences, prior approvals, learned supplier notes — hydrated at the
start of a run and written back at commit. In this build that's LangGraph's `AsyncDatabricksStore`.
The mental model:

> **Checkpointer** answers *"what happened in this thread?"* — **Store** answers *"what should the
> agent remember across threads, and what similar memories should it recall right now?"*

Both wrappers generate short-lived OAuth DB credentials through the Databricks SDK and rotate
them — there's no Postgres password in your config or `.env`.

![The agent runtime graph: START → supervisor (history-aware router) fans out to three parallel gather agents (gather_knowledge on Vector Search, gather_analytics on Genie, gather_operational on Lakebase hybrid SQL); they fan in on hydrate_memory (reads the long-term store); then planner → a gate_router that routes to hitl_review (a durable interrupt()) when approval is needed, else straight to commit, which writes memory and operational write-back tables.](../architecture-diagram-3-runtime-graph.png)

*Where memory lives in the graph: short-term history feeds the router and planner; long-term
store is hydrated after the gather phase; commit writes memory back.*

---

## Operational joins with AI search: similarity as one SQL predicate

Here's the move that makes "memory plus operational data" more than a slogan. Because the
operational rows *also* live in Lakebase (synced from Delta), and Postgres has native pgvector,
semantic similarity becomes **one predicate inside an ordinary SQL join** — not a separate index
lookup followed by an app-side join:

```sql
SELECT m.incident_id, m.summary, m.supplier_id, m.sku, m.category,
       i.on_hand_qty, po.open_po_qty,
       round((1 - (m.embedding <=> %(q)s::vector))::numeric, 3) AS similarity
FROM public.quality_incidents m
JOIN public.inventory_current i  ON m.sku = i.sku
JOIN public.open_pos          po ON m.supplier_id = po.supplier_id AND m.sku = po.sku
WHERE m.expired_at IS NULL
ORDER BY m.embedding <=> %(q)s::vector
LIMIT 5;
```
<sub>The actual query from `agent_server/tools/operational_tool.py` (`HYBRID_SQL`), kept in sync
with `data/operational/04_verify_hybrid_query.py`.</sub>

The `<=>` operator computes vector distance over `quality_incidents` — a native pgvector table
(1024-d, HNSW, cosine) seeded through the `databricks-gte-large-en` embedding endpoint — and the
JOINs pull live `inventory_current` and `open_pos` rows in the *same statement*. One round trip.
The database does the join. Access is governed at the database, not reassembled in the app.

Two details worth calling out for anyone adapting this:

- **It runs as the app service principal**, so every authenticated user sees the same
  UC-governed data. (Per-user row scoping is intentionally out of scope for the demo; in
  production you'd add Postgres RLS keyed on `current_user()` or an entitlements join.)
- **The agent returns the SQL it ran** (on its result contract), so the join is traceable in the
  MLflow trace and scorable in evaluation. The LLM never invents the numbers — they come from the
  data layer, period.

This is the pattern to reach for when *similarity is one predicate in an operational query*,
rather than the whole job.

---

## When to reach for Lakebase vs. Vector Search vs. Genie

Unifying state on Lakebase doesn't mean everything belongs there. The copilot deliberately uses
**all three** Databricks retrieval surfaces and routes each question to the right one. Here's the
decision framework:

| Dimension | Mosaic AI Vector Search | Lakebase (LangGraph store + SQL) |
|---|---|---|
| Data nature | Large unstructured knowledge corpus | Agent memory + operational records, co-located with entities |
| Vector storage | Managed index over Delta | LangGraph `DatabricksStore` (Postgres `store_vectors`) |
| Operational join | App-side, multi-hop | Native SQL join, single query |
| Row-level access | Index filters / app-side | In-query predicate / Postgres grants |
| Scale | Large corpora (100Ks → 100M), managed HNSW | Small-to-moderate sets co-located with state |
| Managed RAG features | Ingestion, hybrid retrieval, reranking | Not built-in |
| Output | Passages | Rows + operational context |

The routing rules, in one breath:

- **Lakebase + LangGraph** when the agent needs **memory + semantic similarity + live SQL joins in
  one operational query path** — similarity is a predicate *inside* a relational query.
- **Mosaic AI Vector Search** for **large-scale managed RAG** over broad document corpora
  (contracts, SOPs, incident reports) — managed ingestion, hybrid retrieval, reranking, large
  vector counts.
- **Genie** for **structured business questions** — NL→SQL aggregation over governed tables
  (*"total unfulfilled demand by product code for Q4?"*).

In the copilot, a supervisor router classifies each question and fans out to the matching
gather agent (Knowledge → Vector Search, Analytics → Genie, Operational → the Lakebase hybrid
query), in parallel. Same app, three tools, one decision framework for picking between them.

---

## Making it a real app: durable HITL and observability

A planner shouldn't auto-execute a $50K expedite. Because short-term state is durable on
Lakebase, human-in-the-loop approval is almost free: LangGraph's `interrupt()` pauses the run at
a checkpoint, and it resumes — from that exact point — whenever a human responds, even if the app
restarted in between.

```python
def hitl_review_node(state: AgentState) -> dict:
    rec = state.get("recommendation")
    resume_value = interrupt(
        {
            "type": "approval_request",
            "recommendation": rec.model_dump() if rec else None,
            "planned_actions": [a.model_dump() for a in rec.planned_actions] if rec else [],
            "evidence": _evidence_bundle(state),
            "prompt": "Approve or reject this recommendation.",
        }
    )
    # resume_value is whatever the app passed to Command(resume=...): an HITLDecision.
    decision = _coerce_decision(resume_value, state.get("user_id", "unknown"))
    return {"hitl_decision": decision, ...}
```
<sub>From `agent_server/graph/planner.py` (`hitl_review_node`).</sub>

Three design choices make this robust:

- **The gate is deterministic, not a black-box LLM call.** A recommendation needs approval when
  it commits spend / is risky-irreversible, *or* when a known cost clears a threshold. Purely
  informational answers skip the gate. Escalation you can reason about and test.
- **The interrupt lives in its own review node, after fan-in.** Keeping it out of any parallel
  step means a resume doesn't drag sibling tasks back into re-execution.
- **Databricks Apps, not Model Serving.** Planning runs execute in-process as background work
  with the UI polling state. Model Serving times out long synchronous requests; the durable
  checkpoint makes an Apps run resumable across restarts.

And because it's LangGraph on Databricks, **MLflow autologs the whole run as one trace** —
supervisor routing, the three parallel gather calls, the planner, the gate decision, the HITL
pause/resume, and the memory write-back — with model, cost, and latency per span. The operational
agent's returned SQL shows up right in the trace, so routing correctness, join correctness, and
recommendation quality are all scorable.

---

## Try it, and build your own

The whole thing is a reference build you can deploy in one command:

- **Get the code:** [`lakebase-for-ai-developers`](https://github.com/databricks-solutions/lakebase-for-ai-developers) — clone it and run `make deploy PROFILE=<your-profile>` for a full, cold-start-safe deploy (Lakebase project, build, seed, Genie, verify).
- **Start from the template:** the [`agent-langgraph-advanced`](https://github.com/databricks/app-templates/tree/main/agent-langgraph-advanced) app template (LangGraph on Apps + Lakebase memory + MLflow).
- **Go deeper in the docs:** [stateful agents](https://docs.databricks.com/aws/en/generative-ai/agent-framework/stateful-agents), [Lakebase synced tables](https://docs.databricks.com/aws/en/oltp/projects/sync-tables), the [Genie Conversation API](https://docs.databricks.com/aws/en/genie/conversation-api), and [Mosaic AI Vector Search](https://docs.databricks.com/aws/en/generative-ai/vector-search).

Whichever seat you're in:

- **Building an agent?** Clone it and swap in your own data — the pattern (short-term + long-term
  memory + operational joins on one backend) transfers directly.
- **In the field?** The decision framework above is the reusable artifact: when an agent needs
  memory *and* live joins in one governed query path, that's Lakebase's lane.
- **Evaluating the approach?** Start with the one question that stresses it — "find similar past
  cases, joined to live operational data" — and see whether your current stack can answer it in a
  single governed query.

*Built with LangGraph, Lakebase, Mosaic AI Vector Search, Genie, and MLflow on Databricks Apps.*
