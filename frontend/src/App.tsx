import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { getMe, listSessions, sendMessage, upsertSession } from "./api";
import { BlobBg } from "./components/BlobBg";
import { ChatPanel } from "./components/ChatPanel";
import { ExplorerDrawer } from "./components/ExplorerDrawer";
import { Sidebar } from "./components/Sidebar";
import { TourProvider } from "./tour/TourProvider";
import { TourButton } from "./tour/TourButton";
import type { ChatMessage, Me, Session } from "./types";

const newThreadId = () =>
  (crypto.randomUUID?.() ?? `t-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);

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
      const pending: ChatMessage = { id: newThreadId(), role: "assistant", text: "", pending: true };
      const isFirst = (byThread[thread] ?? []).length === 0;
      setByThread((m) => ({ ...m, [thread]: [...(m[thread] ?? []), userMsg, pending] }));
      setBusy(true);
      try {
        const { text: reply, extras } = await sendMessage(text, thread, me?.email ?? null);
        setByThread((m) => ({
          ...m,
          [thread]: (m[thread] ?? []).map((x) => (x.id === pending.id ? { ...x, text: reply, extras, pending: false } : x)),
        }));
        await upsertSession(thread, {
          title: isFirst ? text.slice(0, 80) : undefined,
          preview: reply.slice(0, 160),
          updated_at: new Date().toISOString(),
        }).catch(() => {});
        refreshSessions();
      } catch (e) {
        setByThread((m) => ({
          ...m,
          [thread]: (m[thread] ?? []).map((x) => (x.id === pending.id ? { ...x, text: `Error: ${e}`, pending: false, error: true } : x)),
        }));
      } finally {
        setBusy(false);
      }
    },
    [thread, me, byThread, refreshSessions]
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
          onOpen={(t) => setThread(t)}
        />
        <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", position: "relative", zIndex: 1 }}>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, padding: "var(--space-3) var(--space-5)", borderBottom: "1px solid var(--border)" }}>
            <TourButton tourId="overview" />
            <button data-tour="inspect" onClick={() => setExplorerOpen(true)} style={inspectBtn}>⚙ Inspect backend</button>
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <ChatPanel messages={messages} busy={busy} onSend={onSend} />
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
