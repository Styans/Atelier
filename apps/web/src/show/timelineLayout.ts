// Layout of the program timeline: per scene a transition block and a hold block, in seconds.

import type { CompiledSummary, Scene } from "@/api/types";

export interface Block {
  sceneId: string | null;
  kind: "takeoff" | "transition" | "hold" | "landing";
  start: number;
  duration: number;
  estimated: boolean;
}

/**
 * From the compiled timeline when it is current (exact), otherwise from the program
 * (holds are exact, automatic transitions shown with a nominal length).
 */
export function layoutBlocks(program: Scene[], compiled: CompiledSummary | null, nominalTransition = 8): Block[] {
  if (compiled) {
    return compiled.marks
      .filter((m) => m.kind !== "ground")
      .map((m) => ({
        sceneId: m.scene_id,
        kind: m.kind as Block["kind"],
        start: m.start_frame / compiled.fps,
        duration: (m.end_frame - m.start_frame + 1) / compiled.fps,
        estimated: false,
      }));
  }
  const blocks: Block[] = [];
  let t = 0;
  for (const scene of program) {
    const transition = scene.transition_in.duration_s ?? nominalTransition;
    blocks.push({ sceneId: scene.id, kind: "transition", start: t, duration: transition, estimated: scene.transition_in.duration_s === null });
    t += transition;
    blocks.push({ sceneId: scene.id, kind: "hold", start: t, duration: scene.hold_s, estimated: false });
    t += scene.hold_s;
  }
  return blocks;
}

export function totalDuration(blocks: Block[]): number {
  return blocks.reduce((end, b) => Math.max(end, b.start + b.duration), 0);
}
