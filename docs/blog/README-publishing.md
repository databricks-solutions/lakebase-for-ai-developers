# Publishing checklist — Databricks Community blog

Companion to [`agent-memory-operational-joins-lakebase.md`](agent-memory-operational-joins-lakebase.md).
Based on internal blog-process research (see the planning brief). Work top to bottom.

## Where it goes

- **Canonical home:** Databricks Community → **Community Articles / Knowledge Sharing Hub**
  (a published article, *not* a forum thread), under **community.databricks.com/t5/technical-blog**.
- **Approval:** No formal approval is required for Community posts (unlike the main Databricks
  blog), as long as basic policy is followed — no confidential info, correct licensing, no
  secrets. Marketing promotion (featured placement) is an *optional* separate ask by email
  (~3–10 business days).

## Metadata to set

| Field | Value |
|---|---|
| Category (pick 1) | **Architecture & Design** |
| Tags (3–7, lowercase-hyphenated) | `lakebase`, `agents`, `genai`, `langgraph`, `mlflow`, `vector-search`, `unity-catalog` |
| Length | ~2,000 words — fits the deep-dive/architecture norm (2,000–3,500) |
| Hero image | The architecture PNG (`docs/diagrams/Agentic_Apps_Architecture.001.png`); 1200×630 preferred for OG |

## Before submitting

- [ ] **Render diagrams to images.** Export the Mermaid diagrams from `docs/architecture.md`
      (Diagram 3 = runtime graph, Diagram 4 = permissions) to PNG/SVG; the draft references
      `../architecture-diagram-3-runtime-graph.png` — create it or update the path. The hero PNG
      already exists.
- [ ] **Add alt text + captions** to every figure (accessibility requirement; the draft has alt
      text on the hero and Diagram 3 — mirror it for any added images).
- [ ] **Re-verify each code snippet against source** and keep the `<sub>` file citations accurate:
      - Wiring block → `agent_server/lakebase.py` (`lakebase_context`)
      - Hybrid SQL → `agent_server/tools/operational_tool.py` (`HYBRID_SQL`)
      - HITL → `agent_server/graph/planner.py` (`hitl_review_node`)
- [ ] **Confirm public links resolve:** the repo
      (`github.com/databricks-solutions/lakebase-for-ai-developers`), the `agent-langgraph-advanced`
      template, and the four docs.databricks.com links.
- [ ] **No secrets / credentials / customer data** in any snippet (none present — snippets use
      placeholders like `PROJECT`/`BRANCH`/`SCHEMA`).

## Legal — no triggers, but confirm

None of the planned content trips a legal-review trigger, but verify:

- [ ] No competitor comparisons, benchmarks, or pricing claims (the Lakebase-vs-Vector-Search
      table compares **Databricks-native** options only — fine; keep it that way).
- [ ] No unreleased features or internal-only info.
- [ ] Images are original / repo-owned (they are).

## Optional cross-post

- Community stays **canonical**. If also posting to Medium/LinkedIn, set the canonical URL back to
  the Community post and lead with *"Originally published on Databricks Community."*
- Add UTM params on cross-post links (`utm_source=medium|linkedin`).

## Tone/structure references (match these)

- *AgentOps on Databricks: Operating Production AI Agents*
- *Manage Agent and Tool Sprawl with Unity AI Gateway in Databricks*
- *Leverage the Power of Multiple Genie Spaces Inside an App*

(all on community.databricks.com/t5/technical-blog)
