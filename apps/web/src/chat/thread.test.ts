import { describe, expect, it } from "vitest";

import type { ChatEvent, ChatMessage } from "@/api/types";

import { applyEvent, buildTurns, canRollback, emptyLive } from "./thread";

let id = 0;
const msg = (role: ChatMessage["role"], content: ChatMessage["content"], turn: string, before = 3, after: number | null = null): ChatMessage => ({
  id: ++id,
  show_id: "s",
  seq: id,
  role,
  content,
  turn_job_id: turn,
  revision_before: role === "user" ? null : before,
  revision_after: after,
  created_at: id,
});

describe("buildTurns", () => {
  it("folds tool results into their calls and groups messages by turn", () => {
    const turns = buildTurns([
      msg("user", [{ type: "text", text: "программа" }], "j1"),
      msg("assistant", [{ type: "tool_use", id: "c1", name: "propose_program", input: {} }], "j1"),
      msg("user", [{ type: "tool_result", tool_use_id: "c1", content: "оценка 230 с", is_error: false }], "j1"),
      msg("assistant", [{ type: "text", text: "Готово." }], "j1", 3, 5),
      msg("user", [{ type: "text", text: "спасибо" }], "j2"),
    ]);

    expect(turns.map((t) => t.items.map((i) => i.kind))).toEqual([["user", "assistant", "assistant"], ["user"]]);
    const call = turns[0].items[1];
    expect(call.kind === "assistant" && call.tools[0]).toMatchObject({ name: "propose_program", result: "оценка 230 с", isError: false });
    expect([turns[0].revisionBefore, turns[0].revisionAfter]).toEqual([3, 5]);
    expect(canRollback(turns[0])).toBe(true);
    expect(canRollback(turns[1])).toBe(false);
  });
});

describe("applyEvent", () => {
  const event = (kind: ChatEvent["kind"], payload: Record<string, unknown>, n: number): ChatEvent => ({
    id: n,
    turn_job_id: "j1",
    seq: n,
    kind,
    payload,
  });

  it("builds the live reply from the stream", () => {
    const steps: ChatEvent[] = [
      event("status", { stage: "thinking", step: 1 }, 1),
      event("text", { delta: "Делаю " }, 2),
      event("text", { delta: "программу" }, 3),
      event("tool_start", { id: "c1", name: "propose_program", input: {} }, 4),
      event("tool_result", { id: "c1", text: "ок", is_error: false }, 5),
      event("status", { stage: "thinking", step: 2 }, 6),
      event("done", {}, 7),
    ];
    const live = steps.reduce(applyEvent, emptyLive);

    expect(live.done && live.cursor === 7 && live.text === "").toBe(true);
    expect(live.replies).toEqual([
      { text: "Делаю программу", tools: [{ id: "c1", name: "propose_program", input: {}, result: "ок", isError: false }] },
    ]);
    expect(steps.slice(0, 3).reduce(applyEvent, emptyLive).text).toBe("Делаю программу");
  });
});
