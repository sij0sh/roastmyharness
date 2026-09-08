import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const DEFAULT_URL = "http://user-server:8000/mcp";
const SAFETY_TIMEOUT_MS = 20 * 60 * 1000;

function mcpUrl(): string {
  const raw = (process.env["ASK_USER_MCP_URL"] || "").trim();
  return raw || DEFAULT_URL;
}

function headers(sessionId?: string): Record<string, string> {
  const out: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
  };
  if (sessionId) out["Mcp-Session-Id"] = sessionId;
  return out;
}

function parseMessage(text: string): any {
  const trimmed = text.trim();
  if (trimmed.startsWith("{")) return JSON.parse(trimmed);
  for (const line of text.split("\n")) {
    const data = line.trim();
    if (!data.startsWith("data:")) continue;
    const payload = data.slice(5).trim();
    if (!payload || payload === "[DONE]") continue;
    try {
      return JSON.parse(payload);
    } catch {
      continue;
    }
  }
  throw new Error(
    `MCP response held no data event (HTTP body ${trimmed.length} chars)`
  );
}

async function rpc(
  url: string,
  body: Record<string, unknown>,
  sessionId?: string,
): Promise<{ json: any; sessionId?: string }> {
  const res = await fetch(url, {
    method: "POST",
    headers: headers(sessionId),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const preview = (await res.text()).slice(0, 200);
    throw new Error(`MCP HTTP ${res.status}: ${preview}`);
  }
  const sid = res.headers.get("mcp-session-id") || undefined;
  return { json: parseMessage(await res.text()), sessionId: sid };
}

async function notify(
  url: string,
  body: Record<string, unknown>,
  sessionId?: string,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: headers(sessionId),
    body: JSON.stringify(body),
  });
  if (res.status !== 200 && res.status !== 202) {
    const preview = (await res.text()).slice(0, 200);
    throw new Error(`MCP HTTP ${res.status}: ${preview}`);
  }
  await res.arrayBuffer().catch(() => undefined);
}

let sessionId: string | undefined;
let nextId = 1;

async function callAskUser(question: string): Promise<string> {
  const url = mcpUrl();
  const call = (method: string, params?: Record<string, unknown>) =>
    rpc(
      url,
      {
        jsonrpc: "2.0",
        id: nextId++,
        method,
        params: params || {},
      },
      sessionId,
    );
  if (!sessionId) {
    const init = await call("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "pi-ask-user-shim", version: "1.0.0" },
    });
    sessionId = init.sessionId;
    await notify(
      url,
      { jsonrpc: "2.0", method: "notifications/initialized" },
      sessionId,
    );
  }
  let res;
  try {
    res = await call("tools/call", {
      name: "ask_user",
      arguments: { question },
    });
  } catch (error) {
    sessionId = undefined;
    throw error;
  }
  const result = res.json?.result;
  if (res.json?.error) {
    throw new Error(`ask_user error: ${JSON.stringify(res.json.error)}`);
  }
  const blocks = Array.isArray(result?.content) ? result.content : [];
  const texts = blocks
    .filter((b: any) => b?.type === "text" && typeof b.text === "string")
    .map((b: any) => b.text);
  if (texts.length === 0) throw new Error("ask_user returned no text");
  return texts.join("\n");
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "ask_user",
    label: "Ask User",
    description:
      "Ask the maintainer a question and get their response. " +
      "Use this to agree on a plan before implementing, and to request " +
      "review after each coherent implementation pass. " +
      "Always commit the work before calling this tool.",
    parameters: Type.Object({
      question: Type.String({
        description: "The question, plan summary, or review request.",
      }),
    }),
    async execute(_toolCallId, params, signal) {
      const question = String((params as any)?.question || "").trim();
      if (!question) throw new Error("ask_user needs a question");
      const sources = [AbortSignal.timeout(SAFETY_TIMEOUT_MS)];
      if (signal) sources.push(signal);
      const combined =
        sources.length > 1 && AbortSignal.any
          ? AbortSignal.any(sources)
          : sources[0];
      const reply = await new Promise<string>((resolve, reject) => {
        if (combined.aborted) {
          reject(new Error("ask_user aborted"));
          return;
        }
        const onAbort = () => reject(new Error("ask_user aborted"));
        combined.addEventListener("abort", onAbort, { once: true });
        callAskUser(question).then(
          (text) => {
            combined.removeEventListener("abort", onAbort);
            resolve(text);
          },
          (error) => {
            combined.removeEventListener("abort", onAbort);
            reject(error);
          },
        );
      });
      return {
        content: [{ type: "text", text: reply }],
        details: {},
      };
    },
  });
}
