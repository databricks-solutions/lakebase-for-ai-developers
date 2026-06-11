import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { deleteSession, getMe, getSessionMessages, listSessions, resumeMessage, streamMessage, upsertSession } from "./api";
import { BlobBg } from "./components/BlobBg";
import { ChatPanel } from "./components/ChatPanel";
import { ExplorerDrawer } from "./components/ExplorerDrawer";
import { Sidebar } from "./components/Sidebar";
import { TourProvider } from "./tour/TourProvider";
import { TourButton } from "./tour/TourButton";
import type { ChatMessage, Me, Session } from "./types";

const newThreadId = () =>
  (crypto.randomUUID?.() ?? `t-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);

// Persisted-transcript shape: only the rendered content (drop transient `steps`/`pending`).
const slim = (x: ChatMessage): ChatMessage => ({ id: x.id, role: x.role, text: x.text, extras: x.extras });
// Chat name inferred from the conversation = the first user message, condensed.
const inferTitle = (text: string) => text.replace(/\s+/g, " ").trim().slice(0, 60) || "New conversation";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [thread, setThread] = useState<string>(newThreadId);
  const [byThread, setByThread] = useState<Record<string, ChatMessage[]>>({});
  const [busy, setBusy] = useState(false);
  const [explorerOpen, setExplorerOpen] = useState(false);

  const messages = byThread[thread] ?? [];

  useEffect(() => { getMe().then(setMe).catch(() => {}); }, []);
  const refreshSessions = useCallback(() => {
    listSessions().then((r) => setSessions(r.sessions)).catch(() => {});
  }, []);
  useEffect(() => { refreshSessions(); }, [refreshSessions]);

  const onSend = useCallback(
    async (text: string) => {
      const userMsg: ChatMessage = { id: newThreadId(), role: "user", text };
      const pendingId = newThreadId();
      const pending: ChatMessage = { id: pendingId, role: "assistant", text: "", pending: true, steps: [] };
      const prior = byThread[thread] ?? [];
      const isFirst = prior.length === 0;
      const patch = (fn: (x: ChatMessage) => ChatMessage) =>
        setByThread((m) => ({ ...m, [thread]: (m[thread] ?? []).map((x) => (x.id === pendingId ? fn(x) : x)) }));

      setByThread((m) => ({ ...m, [thread]: [...(m[thread] ?? []), userMsg, pending] }));
      setBusy(true);
      await streamMessage(text, thread, {
        onStep: (label) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), label] })),
        onRoute: (agents, _reasoning) => patch((x) => {
          const line = `Routing → ${agents.join(", ")}`;
          const steps = x.steps ?? [];
          if (steps.includes(line)) return { ...x, route: agents };
          return { ...x, route: agents, steps: [line, ...steps] };
        }),
        onSubstep: (_node, label) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), label] })),
        onTrace: (note) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), note] })),
        onDone: (reply, extras) => {
          patch((x) => ({ ...x, text: reply, extras, pending: false }));
          const transcript: ChatMessage[] = [
            ...prior.map(slim),
            { id: userMsg.id, role: "user", text },
            { id: pendingId, role: "assistant", text: reply, extras },
          ];
          upsertSession(thread, {
            title: isFirst ? inferTitle(text) : undefined,  // set once; backend preserves it
            preview: reply.slice(0, 160),
            updated_at: new Date().toISOString(),
            messages: transcript,
          }).catch(() => {});
          refreshSessions();
        },
        onError: (msg) => patch((x) => ({ ...x, text: `Error: ${msg}`, pending: false, error: true })),
      });
      setBusy(false);
    },
    [thread, byThread, refreshSessions]
  );

  // Open a conversation. For a historical chat not in memory this session, rehydrate its
  // transcript from the store so it's no longer a dead/empty panel.
  const onOpen = useCallback(
    async (t: string) => {
      setThread(t);
      if ((byThread[t] ?? []).length > 0) return;
      try {
        const { messages } = await getSessionMessages(t);
        if (messages?.length) setByThread((m) => ({ ...m, [t]: messages }));
      } catch { /* leave empty — a brand-new or transcript-less thread */ }
    },
    [byThread]
  );

  // Soft-delete a conversation: hide it immediately (optimistic), then flag it server-side
  // (deleted_by_user — the data is retained). If it's the open one, start a fresh thread.
  const onDelete = useCallback(
    async (t: string) => {
      setSessions((prev) => prev.filter((s) => s.thread_id !== t));
      setByThread((m) => { const { [t]: _drop, ...rest } = m; return rest; });
      if (t === thread) setThread(newThreadId());
      try { await deleteSession(t); } catch { /* server best-effort */ }
      refreshSessions();
    },
    [thread, refreshSessions]
  );

  // HITL: approve/reject the awaiting-approval message in the current thread → resume the run.
  const onResume = useCallback(
    async (verdict: "approved" | "rejected") => {
      const msgs = byThread[thread] ?? [];
      const target = [...msgs].reverse().find((m) => m.role === "assistant" && m.extras?.approval_request);
      if (!target) return;
      const patch = (fn: (x: ChatMessage) => ChatMessage) =>
        setByThread((m) => ({ ...m, [thread]: (m[thread] ?? []).map((x) => (x.id === target.id ? fn(x) : x)) }));
      patch((x) => ({ ...x, pending: true, error: false, text: "", extras: undefined, steps: [`Recording ${verdict} decision…`] }));
      setBusy(true);
      await resumeMessage(thread, verdict, {
        onStep: (label) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), label] })),
        onRoute: (agents, _reasoning) => patch((x) => {
          const line = `Routing → ${agents.join(", ")}`;
          const steps = x.steps ?? [];
          if (steps.includes(line)) return { ...x, route: agents };
          return { ...x, route: agents, steps: [line, ...steps] };
        }),
        onSubstep: (_node, label) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), label] })),
        onTrace: (note) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), note] })),
        onDone: (reply, extras) => {
          patch((x) => ({ ...x, text: reply, extras, pending: false }));
          const transcript = msgs.map((x) => (x.id === target.id ? { ...slim(x), text: reply, extras } : slim(x)));
          upsertSession(thread, { preview: reply.slice(0, 160), updated_at: new Date().toISOString(), messages: transcript }).catch(() => {});
          refreshSessions();
        },
        onError: (msg) => patch((x) => ({ ...x, text: `Error: ${msg}`, pending: false, error: true })),
      });
      setBusy(false);
    },
    [thread, byThread, refreshSessions]
  );

  return (
    <TourProvider>
      <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
        <BlobBg />
        <Sidebar
          me={me}
          sessions={sessions}
          currentThread={thread}
          onNew={() => setThread(newThreadId())}
          onOpen={onOpen}
          onDelete={onDelete}
        />
        <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", position: "relative", zIndex: 1 }}>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, padding: "var(--space-3) var(--space-5)", borderBottom: "1px solid var(--border)" }}>
            <TourButton tourId="overview" />
            <button data-tour="inspect" onClick={() => setExplorerOpen(true)} style={inspectBtn}>⚙ Inspect backend</button>
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <ChatPanel messages={messages} busy={busy} onSend={onSend} onResume={onResume} />
          </div>
        </main>
        <ExplorerDrawer open={explorerOpen} onClose={() => setExplorerOpen(false)} />
      </div>
    </TourProvider>
  );
}

const inspectBtn: CSSProperties = {
  font: "inherit", fontWeight: 500, fontSize: "var(--fs-body-sm)", padding: "8px 16px",
  borderRadius: "var(--radius-pill)", border: "1px solid var(--border-strong)",
  background: "var(--bg-canvas)", color: "var(--fg-1)", cursor: "pointer", boxShadow: "var(--shadow-sm)",
};
