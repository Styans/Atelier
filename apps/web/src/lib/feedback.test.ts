import { afterEach, describe, expect, it, vi } from "vitest";

import { answer, confirmAction, notify, snapshot } from "./feedback";

afterEach(() => {
  answer(false);
  vi.useRealTimers();
});

describe("feedback", () => {
  it("resolves a question with the user's answer", async () => {
    const asked = confirmAction({ title: "Экспорт?" });
    expect(snapshot().confirm?.title).toBe("Экспорт?");
    answer(true);
    await expect(asked).resolves.toBe(true);
    expect(snapshot().confirm).toBeNull();
  });

  it("a newer question declines the open one", async () => {
    const first = confirmAction({ title: "A" });
    const second = confirmAction({ title: "B" });
    await expect(first).resolves.toBe(false);
    answer(true);
    await expect(second).resolves.toBe(true);
  });

  it("notices go away by themselves and keep the last four", () => {
    vi.useFakeTimers();
    for (let i = 0; i < 6; i++) notify(`n${i}`);
    expect(snapshot().toasts.map((toast) => toast.message)).toEqual(["n2", "n3", "n4", "n5"]);
    vi.advanceTimersByTime(8000);
    expect(snapshot().toasts).toEqual([]);
  });
});
