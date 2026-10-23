import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import { keys, useEdit } from "@/api/hooks";
import type { Scene, TimelineEstimate, ValidationReport } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/primitives";
import { t } from "@/i18n/ru";
import { confirmAction } from "@/lib/feedback";
import { cn, formatSeconds } from "@/lib/utils";

import { useShowState } from "./context";
import { SceneEditor } from "./SceneEditor";
import { layoutBlocks, totalDuration } from "./timelineLayout";

const COLORS = {
  takeoff: "bg-sky-900/70",
  landing: "bg-sky-900/70",
  transition: "bg-muted",
  hold: "bg-primary/25 border-primary/50",
} as const;

export function Timeline() {
  const { id, view, setViewer, setTab } = useShowState();
  const show = view.show;
  const [editing, setEditing] = useState<Scene | "new" | null>(null);
  const fresh = view.stale.compiled === null;
  const timeline = useQuery({
    queryKey: [...keys.timeline(id), show.revision],
    queryFn: () => api.get<TimelineEstimate>(`/api/shows/${id}/timeline`),
    placeholderData: (previous) => previous,
  });
  const validation = useQuery({
    queryKey: keys.validation(id),
    queryFn: () => api.get<ValidationReport>(`/api/shows/${id}/validation`),
    enabled: Boolean(show.outputs.validation),
  });
  const reorder = useEdit(id, (order: string[], base) => api.post(`/api/shows/${id}/scenes/reorder`, { base_revision: base, order }));
  const remove = useEdit(id, (sceneId: string, base) =>
    api.del(`/api/shows/${id}/scenes/${sceneId}${base !== undefined ? `?base_revision=${base}` : ""}`),
  );

  const blocks = layoutBlocks(timeline.data);
  const total = totalDuration(blocks);
  const budget = show.brief.duration_s;
  const music = timeline.data?.music_end_s ?? null;
  const exact = timeline.data?.compiled ?? false;
  const span = Math.max(total, budget, music ?? 0, 1);
  const violations = fresh && view.stale.validation === null ? validation.data?.violations ?? [] : [];
  const seek = (time: number) => {
    setViewer({ mode: "show", seek: time });
    setTab("viewer");
  };
  const ids = show.program.map((s) => s.id);
  const move = (index: number, delta: number) => {
    const order = [...ids];
    [order[index], order[index + delta]] = [order[index + delta], order[index]];
    reorder.mutate(order);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-sm">
          {t("timeline.total")}: <b className={cn(total > budget && "text-warning")}>{formatSeconds(total)}</b> · {t("timeline.budget")}{" "}
          {formatSeconds(budget)}
          {total > budget && <span className="text-warning"> ({t("timeline.overBudget", { time: formatSeconds(total - budget) })})</span>}
          {music !== null && ` · ${t("timeline.music")} ${formatSeconds(music)}`}
          <span className="ml-2 text-xs text-muted-foreground">{exact ? t("timeline.compiled") : t("timeline.planned")}</span>
        </div>
        <Button size="sm" onClick={() => setEditing("new")} disabled={Object.keys(show.models).length === 0}>
          <Plus className="h-4 w-4" /> {t("timeline.add")}
        </Button>
      </div>

      <div className="relative h-20 rounded border bg-background">
        {blocks.map((b, i) => {
          const scene = show.program.find((s) => s.id === b.sceneId);
          return (
            <button
              key={i}
              className={cn("absolute top-5 h-12 overflow-hidden border-r px-1 text-left text-[10px]", COLORS[b.kind], b.estimated && "opacity-60")}
              style={{ left: `${(b.start / span) * 100}%`, width: `${(b.duration / span) * 100}%` }}
              title={`${b.kind} ${scene?.title ?? b.title} ${formatSeconds(b.duration)}${b.estimated ? ` (${t("timeline.estimate")})` : ""}`}
              onClick={() => (scene ? setEditing(scene) : seek(b.start))}
            >
              {b.kind === "hold" ? scene?.title : ""}
            </button>
          );
        })}
        <div className="absolute inset-y-0 border-l-2 border-dashed border-warning" style={{ left: `${(budget / span) * 100}%` }} title={t("timeline.budget")} />
        {music !== null && (
          <div className="absolute inset-y-0 border-l-2 border-dotted border-sky-400" style={{ left: `${(music / span) * 100}%` }} title={t("timeline.music")} />
        )}
        {violations.map((v, i) => (
          <button
            key={i}
            className="absolute top-1 h-3 min-w-[3px] rounded-sm bg-destructive"
            style={{ left: `${(v.t_start_s / span) * 100}%`, width: `${((v.t_end_s - v.t_start_s) / span) * 100}%` }}
            title={v.message}
            onClick={() => seek(v.t_start_s)}
          />
        ))}
      </div>

      {show.program.length === 0 && <p className="text-sm text-muted-foreground">{t("timeline.empty")}</p>}
      <div className="space-y-1">
        {show.program.map((scene, index) => (
          <div key={scene.id} className="flex items-center gap-2 rounded border px-2 py-1 text-sm">
            <span className="w-10 text-xs text-muted-foreground">{scene.id}</span>
            <span className="flex-1 truncate">{scene.title}</span>
            <span className="text-xs text-muted-foreground">{show.models[scene.model_id]?.name}</span>
            <Badge>
              {t("timeline.transition")} {scene.transition_in.duration_s ? `${scene.transition_in.duration_s} с` : t("timeline.auto")}
            </Badge>
            <Badge tone="primary">
              {t("timeline.hold")} {scene.hold_s} с
            </Badge>
            {scene.rotation_deg.some((v) => v !== 0) && (
              <Badge title={t("scene.rotation")}>↻ {scene.rotation_deg.join(", ")}°</Badge>
            )}
            {scene.offset_m.some((v) => v !== 0) && (
              <Badge title={t("scene.offset")}>⇄ {scene.offset_m.join(", ")} м</Badge>
            )}
            {(scene.animation?.tracks.length ?? 0) > 0 && <Badge>{scene.animation!.tracks.map((tr) => tr.primitive).join(", ")}</Badge>}
            <Button size="icon" variant="ghost" title={t("scene.up")} disabled={index === 0} onClick={() => move(index, -1)}>
              <ArrowUp className="h-4 w-4" />
            </Button>
            <Button size="icon" variant="ghost" title={t("scene.down")} disabled={index === ids.length - 1} onClick={() => move(index, 1)}>
              <ArrowDown className="h-4 w-4" />
            </Button>
            <Button size="icon" variant="ghost" title={t("common.edit")} onClick={() => setEditing(scene)}>
              <Pencil className="h-4 w-4" />
            </Button>
            <Button size="icon" variant="ghost" title={t("common.delete")} onClick={async () => {
                const ok = await confirmAction({
                  title: t("scene.delete.title", { title: scene.title }),
                  message: t("scene.delete.confirm"),
                  confirmLabel: t("common.delete"),
                  destructive: true,
                });
                if (ok) remove.mutate(scene.id);
              }}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
      </div>

      {violations.length > 0 && (
        <div className="space-y-0.5 text-xs">
          {violations.slice(0, 30).map((v, i) => (
            <button key={i} className="block w-full truncate text-left text-destructive hover:underline" onClick={() => seek(v.t_start_s)}>
              {formatSeconds(v.t_start_s)} {v.scene_id ?? v.segment}: {v.message}
            </button>
          ))}
        </div>
      )}
      {editing && <SceneEditor scene={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
