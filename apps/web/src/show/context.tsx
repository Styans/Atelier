import { createContext, useContext } from "react";

import type { ShowView } from "@/api/types";

export type ViewerMode = "model" | "points" | "show";
export type WorkspaceTab = "viewer" | "timeline" | "pipeline" | "revisions";

export interface ShowState {
  id: string;
  view: ShowView;
  viewer: { mode: ViewerMode; modelId: string | null; seek: number | null };
  setViewer: (update: Partial<ShowState["viewer"]>) => void;
  tab: WorkspaceTab;
  setTab: (tab: WorkspaceTab) => void;
}

export const ShowContext = createContext<ShowState | null>(null);

export function useShowState(): ShowState {
  const state = useContext(ShowContext);
  if (!state) throw new Error("useShowState outside ShowContext");
  return state;
}

/** Is the artifact in ``slot`` missing or stale? (reason, or null when current) */
export function staleReason(view: ShowView, slot: string): string | null | undefined {
  return slot in view.stale ? view.stale[slot] : undefined;
}
