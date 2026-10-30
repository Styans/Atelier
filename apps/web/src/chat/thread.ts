// The chat as the user reads it: turns of the assistant with their tool calls folded in, and
// the live state of the turn being streamed (built from chat events).

import type { ChatEvent, ChatMessage } from "@/api/types";

export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
  result?: string;
  isError?: boolean;
}

export type Item =
  | { kind: "user"; key: string; text: string }
  | { kind: "assistant"; key: string; text: string; tools: ToolCall[] }
  | { kind: "notice"; key: string; text: string };

export interface Turn {
  id: string | null; // the assistant_turn job; null for messages without one
  items: Item[];
  revisionBefore: number | null;
  revisionAfter: number | null;
  rolledBack: boolean;
}

const textOf = (message: ChatMessage) =>
  message.content
    .filter((b): b is { type: "text"; text: string } => b.type === "text")
    .map((b) => b.text)
    .join("");

/** Messages grouped by turn; a user message that carries tool results is folded into the calls. */
export function buildTurns(messages: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  const calls = new Map<string, ToolCall>();
  for (const message of messages) {
    let turn = turns[turns.length - 1];
    // The user's own words open a turn; the assistant's messages of the same job follow it.
    const opens = message.role === "user" && textOf(message) !== "";
    if (!turn || opens || turn.id !== message.turn_job_id) {
      turn = { id: message.turn_job_id, items: [], revisionBefore: null, revisionAfter: null, rolledBack: false };
      turns.push(turn);
    }
    if (message.rolled_back_at !== null) turn.rolledBack = true;
    turn.revisionBefore ??= message.revision_before;
    if (message.revision_after !== null) turn.revisionAfter = message.revision_after;
    const key = `m${message.id}`;
    if (message.role === "notice") {
      turn.items.push({ kind: "notice", key, text: textOf(message) });
    } else if (message.role === "assistant") {
      const tools = message.content.flatMap((b) =>
        b.type === "tool_use" ? [{ id: b.id, name: b.name, input: b.input }] : [],
      );
      for (const tool of tools) calls.set(tool.id, tool);
      turn.items.push({ kind: "assistant", key, text: textOf(message), tools });
    } else {
      for (const block of message.content) {
        if (block.type === "tool_result") {
          const call = calls.get(block.tool_use_id);
          if (call) Object.assign(call, { result: block.content, isError: block.is_error });
        }
      }
      const text = textOf(message);
      if (text) turn.items.push({ kind: "user", key, text });
    }
  }
  return turns;
}

/** A finished turn that changed the show can be rolled back to where it started. */
export function canRollback(turn: Turn): boolean {
  return (
    turn.id !== null &&
    !turn.rolledBack &&
    turn.revisionBefore !== null &&
    turn.revisionAfter !== null &&
    turn.revisionAfter > turn.revisionBefore
  );
}

export interface Reply {
  text: string;
  tools: ToolCall[];
}

export interface Live {
  replies: Reply[]; // earlier model steps of this turn (the history has them only when it ends)
  text: string; // the reply being written now
  tools: ToolCall[];
  stage: string | null;
  done: boolean;
  error: string | null;
  cursor: number;
}

export const emptyLive: Live = { replies: [], text: "", tools: [], stage: null, done: false, error: null, cursor: 0 };

export function applyEvent(live: Live, event: ChatEvent): Live {
  const next = { ...live, cursor: Math.max(live.cursor, event.id) };
  const p = event.payload;
  switch (event.kind) {
    case "status": {
      // A new model step starts a new reply; the one before stays on screen.
      const stage = String(p.stage ?? "");
      if (Number(p.step ?? 1) > 1 && (next.text || next.tools.length)) {
        return { ...next, stage, replies: [...next.replies, { text: next.text, tools: next.tools }], text: "", tools: [] };
      }
      return { ...next, stage };
    }
    case "text":
      return { ...next, text: next.text + String(p.delta ?? ""), stage: "writing" };
    case "tool_start":
      return {
        ...next,
        stage: "tool",
        tools: [...next.tools, { id: String(p.id), name: String(p.name), input: (p.input as Record<string, unknown>) ?? {} }],
      };
    case "tool_result":
      return {
        ...next,
        tools: next.tools.map((t) =>
          t.id === String(p.id) ? { ...t, result: String(p.text ?? ""), isError: Boolean(p.is_error) } : t,
        ),
      };
    case "done":
      return { ...next, done: true, stage: null };
    case "error":
      return { ...next, done: true, stage: null, error: String(p.message ?? "ошибка") };
  }
  return next;
}
