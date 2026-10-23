// Layout of the program timeline: blocks in seconds from the server's timeline (ADR-038),
// exact when the show is compiled, otherwise estimated by the core.

import type { TimelineEstimate } from "@/api/types";

export interface Block {
  sceneId: string | null;
  kind: "takeoff" | "transition" | "hold" | "landing";
  title: string;
  start: number;
  duration: number;
  estimated: boolean;
}

export function layoutBlocks(timeline: TimelineEstimate | undefined): Block[] {
  return (timeline?.segments ?? []).map((s) => ({
    sceneId: s.scene_id,
    kind: s.kind,
    title: s.title,
    start: s.start_s,
    duration: s.duration_s,
    estimated: s.basis !== "exact" && s.basis !== "fixed",
  }));
}

export function totalDuration(blocks: Block[]): number {
  return blocks.reduce((end, b) => Math.max(end, b.start + b.duration), 0);
}
