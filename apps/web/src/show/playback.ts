// Playback of compiled frames: find the two keyframes around a time and interpolate.

export function frameAt(times: Float32Array, time: number): { index: number; alpha: number } {
  const last = times.length - 1;
  if (last <= 0 || time <= times[0]) return { index: 0, alpha: 0 };
  if (time >= times[last]) return { index: last, alpha: 0 };
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= time) lo = mid;
    else hi = mid;
  }
  return { index: lo, alpha: (time - times[lo]) / (times[lo + 1] - times[lo]) };
}

/** Positions of every drone at ``time`` into ``out`` (length drones * 3). */
export function interpolate(
  positions: Float32Array,
  times: Float32Array,
  drones: number,
  time: number,
  out: Float32Array,
): number {
  const { index, alpha } = frameAt(times, time);
  const stride = drones * 3;
  const a = index * stride;
  const b = Math.min(index + 1, times.length - 1) * stride;
  for (let i = 0; i < stride; i++) out[i] = positions[a + i] + (positions[b + i] - positions[a + i]) * alpha;
  return index;
}

/** Red below the minimum distance, through yellow, to green at 1.5 x the minimum. */
export function heatColor(distance: number, minimum: number): [number, number, number] {
  if (distance < minimum - 1e-6) return [1, 0.15, 0.1];
  const k = Math.min(1, (distance - minimum) / (0.5 * minimum));
  return k < 0.5 ? [1, 0.6 + 0.8 * k, 0.1] : [1 - 1.6 * (k - 0.5), 1, 0.2];
}
