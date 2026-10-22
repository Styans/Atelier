import { describe, expect, it } from "vitest";

import type { CompiledSummary, Scene } from "@/api/types";

import { layoutBlocks, totalDuration } from "./timelineLayout";

const scene = (id: string, hold: number, transition: number | null): Scene => ({
  id,
  title: id,
  model_id: "m",
  hold_s: hold,
  transition_in: { duration_s: transition, planner: null, params: {} },
  animation: null,
  offset_m: [0, 0, 0],
  rotation_deg: [0, 0, 0],
});

describe("layoutBlocks", () => {
  it("plans from the program, estimating automatic transitions", () => {
    const blocks = layoutBlocks([scene("s_1", 10, null), scene("s_2", 5, 4)], null, 8);

    expect(blocks.map((b) => [b.kind, b.start, b.duration, b.estimated])).toEqual([
      ["transition", 0, 8, true],
      ["hold", 8, 10, false],
      ["transition", 18, 4, false],
      ["hold", 22, 5, false],
    ]);
    expect(totalDuration(blocks)).toBe(27);
  });

  it("uses the compiled marks when available", () => {
    const compiled = {
      fps: 10,
      marks: [
        { kind: "ground", scene_id: null, title: "", start_frame: 0, end_frame: 0 },
        { kind: "takeoff", scene_id: null, title: "", start_frame: 1, end_frame: 40 },
        { kind: "hold", scene_id: "s_1", title: "", start_frame: 41, end_frame: 90 },
      ],
    } as CompiledSummary;

    const blocks = layoutBlocks([], compiled);

    expect(blocks.map((b) => [b.kind, b.start, b.duration])).toEqual([
      ["takeoff", 0.1, 4],
      ["hold", 4.1, 5],
    ]);
  });
});
