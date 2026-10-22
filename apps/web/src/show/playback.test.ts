import { describe, expect, it } from "vitest";

import { frameAt, heatColor, interpolate } from "./playback";

describe("playback", () => {
  const times = new Float32Array([0, 0.1, 0.2, 0.3]);

  it("finds the keyframes around a time", () => {
    expect(frameAt(times, -1)).toEqual({ index: 0, alpha: 0 });
    expect(frameAt(times, 0.15).index).toBe(1);
    expect(frameAt(times, 0.15).alpha).toBeCloseTo(0.5);
    expect(frameAt(times, 9)).toEqual({ index: 3, alpha: 0 });
  });

  it("interpolates every drone", () => {
    // 2 drones, 4 keyframes; drone 0 moves along x by 1 per keyframe.
    const positions = new Float32Array(4 * 2 * 3);
    for (let f = 0; f < 4; f++) positions[f * 6] = f;
    const out = new Float32Array(6);

    interpolate(positions, times, 2, 0.25, out);

    expect(out[0]).toBeCloseTo(2.5);
    expect(out[3]).toBe(0);
  });

  it("colours the spacing heatmap", () => {
    expect(heatColor(1.5, 2)).toEqual([1, 0.15, 0.1]);
    expect(heatColor(3, 2)[1]).toBe(1);
  });
});
