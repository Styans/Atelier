import { Suspense, lazy, useState } from "react";

import { useShow } from "@/api/hooks";
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

export function ShowPage({ id }: { id: string }) {
  const show = useShow(id);
  const [viewer, setViewerState] = useState<ShowState["viewer"]>({ mode: "show", modelId: null, seek: null });
  const [tab, setTab] = useState<WorkspaceTab>("viewer");

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
      <div className="grid h-full grid-cols-[300px_minmax(0,1fr)_300px] grid-rows-[minmax(0,1fr)_auto]">
        <aside className="row-span-2 overflow-y-auto border-r p-3">
          <h2 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">{t("show.materials")}</h2>
          <Materials />
        </aside>
        <section className="flex min-h-0 flex-col">
          <Tabs value={tab} onValueChange={(v) => setTab(v as WorkspaceTab)} className="flex min-h-0 flex-1 flex-col">
            <TabsList>
              <TabsTrigger value="viewer">{t("show.tab.viewer")}</TabsTrigger>
              <TabsTrigger value="timeline">{t("show.tab.timeline")}</TabsTrigger>
              <TabsTrigger value="pipeline">{t("show.tab.pipeline")}</TabsTrigger>
              <TabsTrigger value="revisions">{t("show.tab.revisions")}</TabsTrigger>
              <TabsTrigger value="chat">{t("show.tab.chat")}</TabsTrigger>
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
            <TabsContent value="chat" className="p-6 text-muted-foreground">
              {t("show.chat.later")}
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
