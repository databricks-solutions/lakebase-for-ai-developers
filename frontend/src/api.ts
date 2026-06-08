import type { AgentExtras, ExplorerData, Me, Session } from "./types";

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
  body: { title?: string; preview?: string; updated_at?: string }
): Promise<Session> =>
  fetch(`/api/sessions/${encodeURIComponent(threadId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(jsonOrThrow);

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
