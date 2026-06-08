# frontend/ — Supply-Chain Planner Copilot UI (WS1 + WS4)

A one-page, **Claude-style** chat interface to the LangGraph agent, in the Databricks **V0**
design language (ported from `strategic_revenue_demo`'s `databricks-horizontal-demo-app-arch`
skill). Plus a **Backend Explorer** so anyone can peer into every Databricks component the agent
runs on.

> Replaces the earlier "clone the Next.js `e2e-chatbot-app-next` on demand" placeholder: this is
> a committed, custom React+Vite+TS SPA served by the agent server itself (no separate service).

## What's here

- **Chat** (`components/ChatPanel.tsx`) — Claude-like single column; calls the agent's
  `/invocations` and renders the assistant reply + structured extras (route, generated SQL,
  trace steps, and an HITL approval card when the gate trips).
- **Backend Explorer** (`components/ExplorerDrawer.tsx`) — a card per component, each with a
  deep link into Databricks **and** an inline "peek":
  - **Lakebase** (autoscaling Postgres — agent memory + operational rows) → opens the instance
  - **pgvector** (`quality_incidents`, vector(1024)+HNSW) → live row peek
  - **MLflow Experiment** (one trace per run) → opens the experiment
  - **Vector Search** (Knowledge/RAG index) → opens the index
  - **Genie Space** (NL→SQL analytics) → opens the space
  - **Unity Catalog** tables → live list (queried **on-behalf-of you**) + Catalog Explorer link
- **History + identity** (`components/Sidebar.tsx`) — the signed-in user (OBO) and their past
  conversations. Each chat is one Lakebase thread (`thread_id`); the agent keeps context in the
  Lakebase checkpoint, so reopening a conversation resumes it server-side.

## How it connects

The UI is served by the **same** agent server (`agent_server/webapp.py` mounts it at `/ui` and
adds `/api/me`, `/api/sessions`, `/api/explorer/*`). One Databricks App, graph in-process — no
separate service. Auth is **OBO**: Databricks Apps forward the caller's token, and UC reads in
the Explorer run as that user.

## Run it locally

Two terminals against your `.env` (foshizzle) workspace:

```bash
# 1) agent server (FastAPI) on :8000
uv run start-server

# 2) Vite dev server on :5173 (proxies /api + /invocations → :8000)
npm --prefix frontend install
npm --prefix frontend run dev          # open http://localhost:5173
```

Local dev has no Apps proxy, so `/api/me` falls back to `DEMO_PLANNER_USER` (your `.env`) — which
is also the in-scope operational identity, so the hero scenario works.

## Build for deploy

```bash
npm --prefix frontend ci && npm --prefix frontend run build   # → frontend/dist
uv run start-server                                           # serves the SPA at /ui
```

On Databricks Apps the build runs at deploy time; the app then serves `/ui`. To make the UI the
App root instead of `/ui`, set `enable_chat_proxy=False` in `start_server.py` and change the
mount path in `webapp.py` to `/`.

## Design source

Tokens in `src/styles/tokens.css` are the Databricks V0 palette (lava / navy / oat, DM Sans +
DM Mono). Fonts load from Google Fonts (see `index.html`). Background blobs, eyebrow chips, and
card treatment follow the `databricks-horizontal-demo-app-arch` skill.

## Known TODOs

- **HITL approve/reject** buttons are placeholders — wiring resume + streaming the approval card
  is follow-up #2 (`custom_outputs` on the `/responses` path).
- **Transcript rehydration**: reopening a past conversation resumes context server-side but the
  visible message list is per-browser-session; a `/api/sessions/{id}/messages` endpoint that
  reads the checkpoint would render full history across reloads.
- **Streaming**: v1 uses the sync `/invocations`; switch to the run/poll/resume + SSE path for
  token-by-token streaming and live step updates.
