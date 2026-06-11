import type { AgentExtras, ChatMessage, ExplorerData, Me, Session } from "./types";

async function jsonOrThrow(r: Response) {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
  return r.json();
}

export const getMe = (): Promise<Me> => fetch("/api/me").then(jsonOrThrow);

export const getExplorer = (): Promise<ExplorerData> =>
  fetch("/api/explorer").then(jsonOrThrow);

export const peek = (path: string): Promise<any> => fetch(path).then(jsonOrThrow);

export const listSessions = (): Promise<{ sessions: Session[] }> =>
  fetch("/api/sessions").then(jsonOrThrow);

export const upsertSession = (
  threadId: string,
  body: { title?: string; preview?: string; updated_at?: string; messages?: unknown[] }
): Promise<Session> =>
  fetch(`/api/sessions/${encodeURIComponent(threadId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(jsonOrThrow);

/** Rehydrate a past conversation's transcript (so clicking a historical session reopens it). */
export const getSessionMessages = (threadId: string): Promise<{ messages: ChatMessage[] }> =>
  fetch(`/api/sessions/${encodeURIComponent(threadId)}/messages`).then(jsonOrThrow);

/** Pull all assistant text out of an MLflow ResponsesAgentResponse `output` array. */
function extractText(output: any[]): string {
  const parts: string[] = [];
  for (const item of output ?? []) {
    if (typeof item?.text === "string") parts.push(item.text);
    if (item?.content) {
      for (const c of Array.isArray(item.content) ? item.content : [item.content]) {
        if (typeof c?.text === "string") parts.push(c.text);
        else if (typeof c === "string") parts.push(c);
      }
    }
  }
  return parts.join("").trim();
}

/**
 * Streaming turn: POSTs to the SSE endpoint and emits live step progress as the graph runs,
 * then the final answer + extras. Identity (OBO) + access scope are derived server-side from the
 * forwarded token, so we only send the question + thread_id.
 */
interface StreamHandlers {
  onStep?: (label: string) => void;
  onSubstep?: (node: string, label: string) => void;
  onTrace?: (note: string) => void;
  onRoute?: (agents: string[], reasoning: string) => void;
  onDone: (text: string, extras: AgentExtras) => void;
  onError: (msg: string) => void;
}

async function consumeSSE(payload: Record<string, unknown>, handlers: StreamHandlers): Promise<void> {
  let res: Response;
  try {
    res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    handlers.onError(String(e));
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError(`${res.status} ${res.statusText}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      let obj: any;
      try { obj = JSON.parse(line.slice(5).trim()); } catch { continue; }
      if (obj.type === "step" && obj.label) handlers.onStep?.(obj.label);
      else if (obj.type === "done") handlers.onDone(obj.text || "(no text)", { ...(obj.extras ?? {}), trace_id: obj.trace_id } as AgentExtras);
      else if (obj.type === "error") handlers.onError(obj.error || "stream error");
      else if (obj.type === "substep" && obj.label) handlers.onSubstep?.(obj.node ?? "", obj.label);
      else if (obj.type === "trace" && obj.note) handlers.onTrace?.(obj.note);
      else if (obj.type === "route" && Array.isArray(obj.agents)) handlers.onRoute?.(obj.agents, obj.reasoning ?? "");
    }
  }
}

/** New turn — streams step progress then the final answer. */
export const streamMessage = (text: string, threadId: string, handlers: StreamHandlers): Promise<void> =>
  consumeSSE({ question: text, thread_id: threadId }, handlers);

/** Resume a paused HITL run with the approval verdict — streams the commit then the result. */
export const resumeMessage = (
  threadId: string,
  verdict: "approved" | "rejected",
  handlers: StreamHandlers
): Promise<void> => consumeSSE({ thread_id: threadId, verdict }, handlers);

/** Log 👍/👎 feedback on a run's MLflow trace. */
export const sendFeedback = (traceId: string, value: boolean, comment?: string): Promise<{ ok: boolean }> =>
  fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trace_id: traceId, value, comment }),
  })
    .then((r) => r.json())
    .catch(() => ({ ok: false }));

/**
 * Send one turn to the agent. Context is server-side: we pass thread_id (Lakebase checkpoint)
 * and user_id, and only the latest message — the graph resumes prior turns from its checkpoint.
 */
export async function sendMessage(
  text: string,
  threadId: string,
  userId: string | null
): Promise<{ text: string; extras: AgentExtras }> {
  const res = await fetch("/invocations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      input: [{ role: "user", content: text }],
      custom_inputs: { thread_id: threadId, ...(userId ? { user_id: userId } : {}) },
    }),
  });
  const data = await jsonOrThrow(res);
  return {
    text: extractText(data.output) || "(no text returned)",
    extras: (data.custom_outputs ?? {}) as AgentExtras,
  };
}
