import { useQuery } from "@tanstack/react-query";
import { Check, Download, Hammer, Play, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { api, fileUrl } from "@/api/client";
import { keys, useEdit, useRunJob } from "@/api/hooks";
import type { ExportInfo, ValidationReport } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/primitives";
import { type MessageKey, plural, t, tOr } from "@/i18n/ru";
import { formatSeconds } from "@/lib/utils";

import { useShowState } from "./context";

type Tone = "ok" | "warning" | "default" | "error";

function StageCard({
  stage,
  state,
  tone,
  children,
}: {
  stage: string;
  state: string;
  tone: Tone;
  children?: ReactNode;
}) {
  const { id, view } = useShowState();
  const approve = useEdit(id, (status: string, base) =>
    api.post(`/api/shows/${id}/stages/${stage}/approve`, { base_revision: base, status }),
  );
  const approved = view.show.stage_status[stage] === "approved";
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t(`stage.${stage}` as MessageKey)}</CardTitle>
        <div className="flex items-center gap-1">
          <Badge tone={approved ? "ok" : tone}>{approved ? t("stage.approved") : state}</Badge>
          <Button
            size="icon"
            variant={approved ? "secondary" : "ghost"}
            title={t("stage.approve")}
            onClick={() => approve.mutate(approved ? "in_progress" : "approved")}
          >
            <Check className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>
      {children && <CardContent className="pt-1">{children}</CardContent>}
    </Card>
  );
}

export function Studio() {
  const { id, view } = useShowState();
  const show = view.show;
  const runJob = useRunJob(id);
  const drones = show.brief.fleet.drone_count;
  const models = Object.values(show.models);
  const pointSlots = models.map((m) => `models.${m.id}.points.${m.drone_count ?? drones}`);
  const pointsReady = pointSlots.filter((s) => view.stale[s] === null).length;
  const needPoints = models.filter((m, i) => m.assets.mesh && view.stale[pointSlots[i]] !== null);
  const tracks = show.program.reduce((n, s) => n + (s.animation?.tracks.length ?? 0), 0);
  const plannedHold = show.program.reduce((n, s) => n + s.hold_s, 0);
  const compiledState = view.stale.compiled;
  const validationState = view.stale.validation;
  // Either the compiled show or the report itself is out of date (undefined: never produced).
  const stale = Boolean(compiledState) || Boolean(validationState);
  const running = view.jobs.some((j) => j.status === "running" || j.status === "queued");

  const validation = useQuery({
    queryKey: keys.validation(id),
    queryFn: () => api.get<ValidationReport>(`/api/shows/${id}/validation`),
    enabled: Boolean(show.outputs.validation),
  });
  const exports = useQuery({
    queryKey: keys.exports(id),
    queryFn: () => api.get<ExportInfo[]>(`/api/shows/${id}/exports`),
    enabled: Object.keys(show.outputs.exports).length > 0,
  });

  return (
    <div className="space-y-2">
      <StageCard stage="brief" state={show.brief.title} tone="default" />
      <StageCard stage="program" state={`${show.program.length} · ${formatSeconds(plannedHold)}`} tone={show.program.length ? "default" : "warning"} />
      <StageCard stage="models" state={`${models.filter((m) => m.assets.mesh).length}/${models.length}`} tone={models.length ? "default" : "warning"} />
      <StageCard stage="points" state={`${pointsReady}/${models.length}`} tone={models.length === 0 ? "default" : needPoints.length ? "warning" : "ok"}>
        {needPoints.length > 0 && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => needPoints.forEach((m) => runJob.mutate({ kind: "points", args: { model_id: m.id } }))}
          >
            <Play className="h-3.5 w-3.5" /> {t("stage.points.all")}
          </Button>
        )}
      </StageCard>
      <StageCard stage="animation" state={String(tracks)} tone="default" />
      <StageCard
        stage="validation"
        state={
          compiledState === undefined
            ? "—"
            : stale
              ? t("stage.stale")
              : validation.data
                ? validation.data.ok
                  ? t("stage.ok")
                  : plural(validation.data.violations.length, "stage.violations")
                : "—"
        }
        tone={stale ? "warning" : validation.data ? (validation.data.ok ? "ok" : "error") : "default"}
      >
        <div className="flex flex-wrap gap-1">
          <Button size="sm" variant="secondary" disabled={running || show.program.length === 0} onClick={() => runJob.mutate({ kind: "compile" })}>
            <Hammer className="h-3.5 w-3.5" /> {t("stage.compile")}
          </Button>
          <Button size="sm" variant="secondary" disabled={running || compiledState !== null} onClick={() => runJob.mutate({ kind: "validate" })}>
            <ShieldCheck className="h-3.5 w-3.5" /> {t("stage.validate")}
          </Button>
        </div>
        {(compiledState || validationState) && <p className="text-[11px] text-warning">{compiledState || validationState}</p>}
        {validation.data && (
          <div className="text-[11px] text-muted-foreground">
            {Object.entries(validation.data.metrics)
              .filter(([k]) => !k.endsWith("_at_s"))
              .map(([k, v]) => (
                <div key={k}>
                  {tOr(`metric.${k}`, k)}: {v.toFixed(2)}
                </div>
              ))}
          </div>
        )}
      </StageCard>
      <StageCard stage="export" state={String(exports.data?.length ?? 0)} tone="default">
        <Button
          size="sm"
          variant="secondary"
          disabled={running || compiledState !== null}
          onClick={() => window.confirm(t("stage.export.confirm")) && runJob.mutate({ kind: "export" })}
        >
          <Download className="h-3.5 w-3.5" /> {t("stage.export.run")}
        </Button>
        {exports.data?.map((e) => (
          <div key={e.component} className="text-xs">
            <div className="font-medium">{e.component}</div>
            {e.files.map((file) => (
              <a key={file} className="block truncate text-primary hover:underline" href={fileUrl(id, `${e.folder}/${file}`)}>
                {file}
              </a>
            ))}
          </div>
        ))}
      </StageCard>
    </div>
  );
}
