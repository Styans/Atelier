import { type PointerEvent as ReactPointerEvent, Suspense, lazy, useState } from "react";

import { useShow } from "@/api/hooks";
import { ChatPanel } from "@/chat/ChatPanel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { t } from "@/i18n/ru";
import { ShowContext, type ShowState, type WorkspaceTab } from "@/show/context";
import { JobsPanel } from "@/show/JobsPanel";
import { Materials } from "@/show/Materials";
import { PipelinePanel } from "@/show/PipelinePanel";
import { RevisionsPanel } from "@/show/RevisionsPanel";
import { Studio } from "@/show/Studio";
import { Timeline } from "@/show/Timeline";

// three.js and the viewer are most of the bundle: the list of shows and the plugins screen
// load without them, the 3D view fetches them on first use.
const Viewer = lazy(() => import("@/show/Viewer").then((module) => ({ default: module.Viewer })));

const CHAT_WIDTH_KEY = "atelier.chatWidth";
const CHAT_MIN = 280;
const CHAT_MAX = 720;

function storedChatWidth(): number {
  try {
    const value = Number(window.localStorage.getItem(CHAT_WIDTH_KEY));
    return value >= CHAT_MIN && value <= CHAT_MAX ? value : 380;
  } catch {
    return 380; // storage blocked (private window): the default is fine
  }
}

export function ShowPage({ id }: { id: string }) {
  const show = useShow(id);
  const [viewer, setViewerState] = useState<ShowState["viewer"]>({ mode: "show", modelId: null, seek: null });
  const [tab, setTab] = useState<WorkspaceTab>("viewer");
  const [chatWidth, setChatWidth] = useState(storedChatWidth);

  const startResize = (down: ReactPointerEvent) => {
    const startX = down.clientX;
    const startWidth = chatWidth;
    let width = startWidth;
    const move = (e: PointerEvent) => {
      width = Math.min(CHAT_MAX, Math.max(CHAT_MIN, startWidth + e.clientX - startX));
      setChatWidth(width);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      try {
        window.localStorage.setItem(CHAT_WIDTH_KEY, String(width));
      } catch {
        // not remembered: fine
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  if (show.isLoading) return <p className="p-6 text-muted-foreground">{t("common.loading")}</p>;
  if (show.error || !show.data) return <p className="p-6 text-destructive">{String(show.error)}</p>;

  const state: ShowState = {
    id,
    view: show.data,
    viewer,
    setViewer: (update) => setViewerState((current) => ({ ...current, ...update })),
    tab,
    setTab,
  };

  return (
    <ShowContext.Provider value={state}>
      <div
        className="grid h-full grid-rows-[minmax(0,1fr)_auto]"
        style={{ gridTemplateColumns: `300px ${chatWidth}px minmax(0,1fr) 300px` }}
      >
        <aside className="row-span-2 overflow-y-auto border-r p-3">
          <h2 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">{t("show.materials")}</h2>
          <Materials />
        </aside>
        <section className="relative row-span-2 flex min-h-0 flex-col border-r">
          <h2 className="px-3 pt-3 text-xs font-semibold uppercase text-muted-foreground">{t("show.chat")}</h2>
          <div className="min-h-0 flex-1">
            <ChatPanel />
          </div>
          <div
            className="absolute inset-y-0 -right-1 w-2 cursor-col-resize hover:bg-primary/30"
            title={t("show.chat.resize")}
            onPointerDown={startResize}
          />
        </section>
        <section className="flex min-h-0 flex-col">
          <Tabs value={tab} onValueChange={(v) => setTab(v as WorkspaceTab)} className="flex min-h-0 flex-1 flex-col">
            <TabsList>
              <TabsTrigger value="viewer">{t("show.tab.viewer")}</TabsTrigger>
              <TabsTrigger value="timeline">{t("show.tab.timeline")}</TabsTrigger>
              <TabsTrigger value="pipeline">{t("show.tab.pipeline")}</TabsTrigger>
              <TabsTrigger value="revisions">{t("show.tab.revisions")}</TabsTrigger>
            </TabsList>
            <TabsContent value="viewer" className="flex flex-col">
              <Suspense fallback={<p className="p-6 text-muted-foreground">{t("common.loading")}</p>}>
                <Viewer />
              </Suspense>
            </TabsContent>
            <TabsContent value="timeline" className="overflow-y-auto p-3">
              <Timeline />
            </TabsContent>
            <TabsContent value="pipeline" className="overflow-y-auto p-3">
              <PipelinePanel />
            </TabsContent>
            <TabsContent value="revisions" className="overflow-y-auto p-3">
              <RevisionsPanel />
            </TabsContent>
          </Tabs>
        </section>
        <aside className="row-span-2 overflow-y-auto border-l p-3">
          <h2 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">{t("show.studio")}</h2>
          <Studio />
        </aside>
        <footer className="border-t">
          <JobsPanel />
        </footer>
      </div>
    </ShowContext.Provider>
  );
}
