import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { getMe, listSessions, resumeMessage, streamMessage, upsertSession } from "./api";
import { BlobBg } from "./components/BlobBg";
import { ChatPanel } from "./components/ChatPanel";
import { ExplorerDrawer } from "./components/ExplorerDrawer";
import { LakebasePanel } from "./components/LakebasePanel";
import { ReviewPanel } from "./components/ReviewPanel";
import { Sidebar } from "./components/Sidebar";
import { TopNav } from "./components/TopNav";
import { TourProvider } from "./tour/TourProvider";
import { TourButton } from "./tour/TourButton";
import type { ChatMessage, Me, ResumeDecisions, Session } from "./types";

export type Page = "chat" | "review" | "lakebase";

const newThreadId = () =>
  (crypto.randomUUID?.() ?? `t-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);

/** Streaming handler block shared by a new turn and an HITL resume — keeps the two paths consistent. */
function streamHandlers(
  patch: (fn: (x: ChatMessage) => ChatMessage) => void,
  onDone: (reply: string, extras: ChatMessage["extras"]) => void
) {
  return {
    onStep: (label: string) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), label] })),
    onRoute: (agents: string[]) =>
      patch((x) => {
        const line = `Routing → ${agents.join(", ")}`;
        const steps = x.steps ?? [];
        if (steps.includes(line)) return { ...x, route: agents };
        return { ...x, route: agents, steps: [line, ...steps] };
      }),
    onSubstep: (_node: string, label: string) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), label] })),
    onTrace: (note: string) => patch((x) => ({ ...x, steps: [...(x.steps ?? []), note] })),
    onDone: (reply: string, extras: NonNullable<ChatMessage["extras"]>) => onDone(reply, extras),
    onError: (msg: string) => patch((x) => ({ ...x, text: `Error: ${msg}`, pending: false, error: true })),
  };
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [thread, setThread] = useState<string>(newThreadId);
  const [byThread, setByThread] = useState<Record<string, ChatMessage[]>>({});
  const [busy, setBusy] = useState(false);
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [page, setPage] = useState<Page>("chat");
  // Bumped on a successful HITL commit so the Lakebase page re-fetches.
  const [committedAt, setCommittedAt] = useState(0);

  const messages = useMemo(() => byThread[thread] ?? [], [byThread, thread]);

  // In-flight SSE turn. Starting a new turn aborts the previous reader/fetch (defensive — the UI's
  // `busy` guard normally prevents overlap), and unmounting aborts so we don't leak the stream or
  // set state on a torn-down tree.
  const turnRef = useRef<AbortController | null>(null);
  const startTurn = useCallback(() => {
    turnRef.current?.abort();
    const ac = new AbortController();
    turnRef.current = ac;
    return ac;
  }, []);
  useEffect(() => () => turnRef.current?.abort(), []);

  useEffect(() => { getMe().then(setMe).catch(() => {}); }, []);
  const refreshSessions = useCallback(() => {
    listSessions().then((r) => setSessions(r.sessions)).catch(() => {});
  }, []);
  useEffect(() => { refreshSessions(); }, [refreshSessions]);

  // The active thread's paused recommendation: last assistant message carrying an approval_request.
  const pausedMsg = useMemo(
    () => [...messages].reverse().find((m) => m.role === "assistant" && m.extras?.approval_request),
    [messages]
  );
  // The recommendation channel — prefer extras.recommendation, fall back to approval_request.recommendation.
  const pausedRecommendation =
    pausedMsg?.extras?.recommendation ?? pausedMsg?.extras?.approval_request?.recommendation;
  const hasPausedPlan = Boolean(pausedMsg);
  // After commit, the same message no longer carries an approval_request (status flips to completed).
  const reviewCommitted = Boolean(committedAt) && !hasPausedPlan;

  const onSend = useCallback(
    async (text: string) => {
      const userMsg: ChatMessage = { id: newThreadId(), role: "user", text };
      const pendingId = newThreadId();
      const pending: ChatMessage = { id: pendingId, role: "assistant", text: "", pending: true, steps: [] };
      const isFirst = (byThread[thread] ?? []).length === 0;
      const patch = (fn: (x: ChatMessage) => ChatMessage) =>
        setByThread((m) => ({ ...m, [thread]: (m[thread] ?? []).map((x) => (x.id === pendingId ? fn(x) : x)) }));

      setByThread((m) => ({ ...m, [thread]: [...(m[thread] ?? []), userMsg, pending] }));
      const ac = startTurn();
      setBusy(true);
      await streamMessage(
        text,
        thread,
        streamHandlers(patch, (reply, extras) => {
          patch((x) => ({ ...x, text: reply, extras, pending: false }));
          upsertSession(thread, {
            title: isFirst ? text.slice(0, 80) : undefined,
            preview: reply.slice(0, 160),
            updated_at: new Date().toISOString(),
          }).catch(() => {});
          refreshSessions();
        }),
        ac.signal
      );
      if (turnRef.current === ac) setBusy(false); // ignore if a newer turn took over
    },
    [thread, byThread, refreshSessions, startTurn]
  );

  // HITL: resume the awaiting-approval message with per-action decisions. Single source of truth —
  // both the chat inline card and the Review page commit route through here.
  const onResumeStructured = useCallback(
    async (decisions: ResumeDecisions) => {
      const msgs = byThread[thread] ?? [];
      const target = [...msgs].reverse().find((m) => m.role === "assistant" && m.extras?.approval_request);
      if (!target) return;
      const patch = (fn: (x: ChatMessage) => ChatMessage) =>
        setByThread((m) => ({ ...m, [thread]: (m[thread] ?? []).map((x) => (x.id === target.id ? fn(x) : x)) }));
      patch((x) => ({
        ...x,
        pending: true,
        error: false,
        text: "",
        extras: undefined,
        steps: [`Recording ${decisions.verdict} decision…`],
      }));
      const ac = startTurn();
      setBusy(true);
      await resumeMessage(
        thread,
        decisions,
        streamHandlers(patch, (reply, extras) => {
          patch((x) => ({ ...x, text: reply, extras, pending: false }));
          refreshSessions();
          // Mark commit so Review shows the success state and Lakebase re-fetches.
          if (decisions.verdict === "approved") setCommittedAt(Date.now());
        }),
        ac.signal
      );
      if (turnRef.current === ac) setBusy(false); // ignore if a newer turn took over
    },
    [thread, byThread, refreshSessions, startTurn]
  );

  // Inline Reject from the chat card → degenerate per-action payload. Approve is no longer an
  // inline path — committing happens only in Review (rationale + per-action decisions).
  const onResume = useCallback(
    (verdict: "approved" | "rejected") =>
      onResumeStructured({ verdict, rationale: "(rejected from chat)", action_decisions: [] }),
    [onResumeStructured]
  );

  const openThread = useCallback((t: string) => {
    setThread(t);
    setCommittedAt(0);
    setPage("chat");
  }, []);
  const newThread = useCallback(() => {
    setThread(newThreadId());
    setCommittedAt(0);
    setPage("chat");
  }, []);

  return (
    <TourProvider>
      <div style={{ display: "flex", height: "100%", overflow: "hidden" }}>
        <BlobBg />
        <Sidebar
          me={me}
          sessions={sessions}
          currentThread={thread}
          onNew={newThread}
          onOpen={openThread}
        />
        <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", position: "relative", zIndex: 1 }}>
          <div style={topBar}>
            <TopNav page={page} setPage={setPage} reviewBadge={hasPausedPlan} />
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <TourButton tourId="overview" />
              <button data-tour="inspect" onClick={() => setExplorerOpen(true)} style={inspectBtn}>⚙ Inspect backend</button>
            </div>
          </div>
          <div style={{ flex: 1, minHeight: 0, overflowY: page === "chat" ? "hidden" : "auto" }}>
            {page === "chat" && <ChatPanel messages={messages} busy={busy} onSend={onSend} onResume={onResume} onGoToReview={() => setPage("review")} workspaceHost={me?.workspace_host} />}
            {page === "review" && (
              <ReviewPanel
                recommendation={pausedRecommendation}
                evidence={pausedMsg?.extras?.evidence}
                traceId={pausedMsg?.extras?.trace_id ?? null}
                workspaceHost={me?.workspace_host}
                busy={busy}
                hasPausedPlan={hasPausedPlan}
                committed={reviewCommitted}
                onResumeStructured={onResumeStructured}
                onGoToChat={() => setPage("chat")}
                onGoToLakebase={() => setPage("lakebase")}
              />
            )}
            {page === "lakebase" && (
              <LakebasePanel thread={thread} workspaceHost={me?.workspace_host} refreshKey={committedAt} />
            )}
          </div>
        </main>
        <ExplorerDrawer open={explorerOpen} onClose={() => setExplorerOpen(false)} />
      </div>
    </TourProvider>
  );
}

const topBar: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 10,
  padding: "var(--space-3) var(--space-5)",
  borderBottom: "1px solid var(--border)",
};

const inspectBtn: CSSProperties = {
  font: "inherit", fontWeight: 500, fontSize: "var(--fs-body-sm)", padding: "8px 16px",
  borderRadius: "var(--radius-pill)", border: "1px solid var(--border-strong)",
  background: "var(--bg-canvas)", color: "var(--fg-1)", cursor: "pointer", boxShadow: "var(--shadow-sm)",
};
