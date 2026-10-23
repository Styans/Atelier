import { describe, expect, it } from "vitest";

import type { TimelineEstimate } from "@/api/types";

import { layoutBlocks, totalDuration } from "./timelineLayout";

const timeline = {
  segments: [
    { kind: "takeoff", scene_id: null, title: "Takeoff", start_s: 0, duration_s: 7.8, basis: "exact" },
    { kind: "transition", scene_id: "s_1", title: "To A", start_s: 7.8, duration_s: 42, basis: "proxy" },
    { kind: "hold", scene_id: "s_1", title: "A", start_s: 49.8, duration_s: 15, basis: "exact" },
    { kind: "transition", scene_id: "s_2", title: "To B", start_s: 64.8, duration_s: 4, basis: "fixed" },
  ],
  total_s: 68.8,
  budget_s: 60,
  over_budget_s: 8.8,
  compiled: false,
  music_start_s: null,
  music_end_s: null,
  notes: [],
} satisfies TimelineEstimate;

describe("layoutBlocks", () => {
  it("draws the server's timeline and marks what is only estimated", () => {
    const blocks = layoutBlocks(timeline);

    expect(blocks.map((b) => [b.kind, b.sceneId, b.start, b.duration, b.estimated])).toEqual([
      ["takeoff", null, 0, 7.8, false],
      ["transition", "s_1", 7.8, 42, true],
      ["hold", "s_1", 49.8, 15, false],
      ["transition", "s_2", 64.8, 4, false],
    ]);
    expect(totalDuration(blocks)).toBeCloseTo(68.8);
  });

  it("is empty until the timeline has loaded", () => {
    expect(layoutBlocks(undefined)).toEqual([]);
  });
});
