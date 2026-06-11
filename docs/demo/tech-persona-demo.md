# Meridian Supply Chain Planner — Use Case & Demo Summary

*An agentic planning assistant with human-in-the-loop decisions, durable state, and full observability. One app, two audiences.*

---

## The use case

Manufacturing runs on plans that break. A supplier slips, demand spikes, a long-lead component goes on allocation — and a planner has hours, not days, to decide how to protect the line. Today that decision means pulling numbers from several systems, reading a supplier notice buried in an inbox, remembering what worked the last time something similar happened, and making a judgment call under pressure. The reasoning behind that call usually lives in the planner's head and disappears the moment they move on to the next fire.

The Meridian Supply Chain Planner is an agentic assistant that takes on the gathering and the first-draft thinking, then hands the actual decision to a human. It watches for exceptions, assembles the evidence, proposes a concrete mitigation, and then waits. Nothing is executed until a planner approves it. And every decision — together with the reasoning behind it — becomes durable state the system reuses the next time a similar problem appears.

The demo is built around one realistic exception. A long-lead IGBT power module (PM-IG1200) used on the EV inverter line is heading for a stockout in 18 days, and it collides with a force-majeure delay from the primary supplier. Roughly $9.4M of finished goods are exposed. The assistant proposes a four-part plan; the planner decides what actually happens.

## Two audiences, one app

The demo is designed to land with two very different rooms at the same time, because in practice both are present in the buying conversation.

**Supply chain planners** care about the decision. Is the plan sound? Can I adjust it? What does it cost? Is my reasoning on record? They spend their time on the Review page, and they read everything in plain language — no SQL, no internals.

**AI, ML, and Data engineers** care about the system underneath. Is it grounded in real data? Is every run observable and costed? Does it actually learn? Is it governed before a human ever sees its output? They spend their time on the Lakebase and Trace pages, reading raw tables, vectors, and execution spans.

The design choice that serves both is a single dual-lens toggle. The same underlying data renders either as raw Postgres — column names, types, the vector, the similarity query — or as plain-language cards, switched with one click. A planner sees "91% match"; an engineer sees `sim 0.91` and the `embedding <=> :q` query that produced it. Neither audience gets a watered-down version, and the toggle itself becomes a memorable demo line: *here's what your planner sees; here's what's actually in the database.*

## The app experience

The app is four pages, walked in order during a demo.

**Overview** is mission control. It orients the room in about fifteen seconds: the exception, the stakes (stockout countdown, revenue at risk, finished goods exposed), and the assistant's progress shown as a simple left-to-right flow with a clear marker on *Human review — here*. The agent has already done its analysis and is holding. One button starts the review.

**Review** is the decision surface and the heart of the app — covered in detail below.

**Lakebase** shows what the decision changed. It separates short-term state (this run, paused mid-flight, waiting on the human) from long-term state (the durable overrides, decisions, constraints, and remembered decisions that persist between runs). This is also where the engineer/planner lens lives, so the same page reads completely differently depending on who's standing in front of it.

**Trace** shows how it ran. A waterfall lays out every step the assistant took, with the parallel evidence-gathering visible as overlapping bars and the human pause visible as a labeled gap in the timeline. Any step can be clicked to inspect its inputs, outputs, latency, and cost. A final panel shows the evaluation checks that gated the proposal and the human's decision logged as feedback.

## The human-in-the-loop experience

This is where most agent demos fall flat. A single "Approve" button on a finished plan makes the human look like a rubber stamp and hides the one thing that matters — the exercise of judgment. Meridian treats the human-in-the-loop moment as the point where judgment becomes durable, and it's built to feel that way.

The planner sees the assistant's proposal as four discrete actions, each sitting directly beneath the evidence that supports it: what the data showed, what the supplier notice said, and what happened the last time a near-identical shortage occurred. For each action, the planner has real authority, not a binary:

- **Approve** it as proposed.
- **Edit** it — change the expedite quantity, raise or lower the safety-stock target on a slider, adjust the order size.
- **Hold** it — decline a single action without killing the rest of the plan.

Partial decisions are first-class. A planner can approve three actions, hold one, and edit the quantities on two, and the plan adapts. As they decide, a live ledger on the right shows exactly what will be written — staged rows accumulating in amber, marked pending. Before anything commits, the planner has to record a short rationale. It takes a sentence, but it's the thing the system actually remembers.

On commit, the ledger flushes from amber to green, the decisions are written, and the rationale is saved alongside them. The entire interaction takes under a minute and produces a complete, auditable record: who decided, what they decided, when, and — crucially — why.

The detail that makes the experience click is the loop. The proposal the planner is reviewing already stands on a past human decision: the assistant retrieved a near-identical shortage from eight months earlier and used that planner's resolution as a template. The new decision is then saved the same way. So the loop closes on screen — human judgment goes in, human judgment comes out, and each call quietly makes the next one better. That is the difference between an assistant that answers and an assistant that learns from the people who use it.

## The through-line

The demo's state is live across all four pages, and this is what makes the "end to end" claim credible rather than a slideshow. A decision made on Review genuinely propagates. Commit, and the new rows appear in Lakebase and the final execution steps light up in the Trace. Before commit, Lakebase openly shows that nothing has landed yet. Edit the safety-stock number and that exact value flows into the database row and into the remembered summary of the decision. It isn't four screenshots stitched together — it's one run, observed from three angles.

## What each audience walks away believing

For **planners**: the assistant does the legwork and drafts a credible plan, but I stay in control, my edits stick, and for the first time my reasoning is captured instead of lost.

For **engineers**: it's grounded in real data and retrieved memory, every run is fully traced and costed, it's checked by automated evaluations before a human is ever involved, and it demonstrably improves from human feedback.

## What's real vs. illustrative

The interaction patterns, the decision-and-memory loop, the data model, and the observability are all faithful to how the system actually works. Two honest caveats for the technical audience: for demo smoothness, state propagates instantly across pages, whereas in the running system those pages are three surfaces over the same database and propagation is a commit-and-refresh cycle rather than a live push; and the figures in the scenario are representative rather than drawn from a production instance.

## Demo run-of-show

A tight path through the four pages, roughly two minutes of narration:

1. **Overview** — "An IGBT shortage just hit the inverter line. The assistant has already analyzed it and is holding for a human." Point at the flow marker, then start the review.
2. **Review** — walk the three evidence cards, noting that the assistant retrieved a prior decision at high similarity, so its proposal already stands on a past human call. Edit a quantity to show it isn't a rubber stamp. Record the rationale. Commit, and watch the ledger flush to green.
3. **Lakebase** — "Here's what your planner just changed." Flip the engineer/planner lens. Land on the remembered decisions: the call just made now sits next to the one that drove the plan. The loop closed.
4. **Trace** — "And here's how it ran." Point at the overlapping evidence bars (real parallelism), the human pause, and the final steps that exist only because the planner approved. Close on the evaluation checks: observable *and* governed.

Reset from the sidebar in one click for the next room.