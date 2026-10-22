import { useMutation } from "@tanstack/react-query";
import { FileText, Square } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import type { Job } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Badge, Progress } from "@/components/ui/primitives";
import { type MessageKey, plural, t } from "@/i18n/ru";
import { formatSeconds } from "@/lib/utils";

import { useShowState } from "./context";

const TONE = { queued: "default", running: "primary", done: "ok", failed: "error", cancelled: "warning" } as const;

/** What a finished job produced; the last progress message ("assembling") says little. */
function summary(job: Job): string {
  const result = job.result as Record<string, unknown> | unknown[] | null;
  if (job.status !== "done" || !result) return job.error ?? job.message;
  if (job.kind === "compile" && !Array.isArray(result)) {
    const warnings = (result.warnings as string[] | undefined) ?? [];
    return [formatSeconds(Number(result.duration_s)), plural(Number(result.frames), "jobs.frames"), ...warnings].join(" · ");
  }
  if (job.kind === "validate" && !Array.isArray(result)) {
    return result.ok ? t("stage.ok") : plural(Number(result.errors), "stage.violations");
  }
  if (job.kind === "export" && Array.isArray(result)) {
    return (result as { files: string[] }[]).flatMap((r) => r.files).join(", ");
  }
  return job.message;
}

export function JobsPanel() {
  const { view } = useShowState();
  const [log, setLog] = useState<{ job: Job; text: string } | null>(null);
  const cancel = useMutation({ mutationFn: (jobId: string) => api.post(`/api/jobs/${jobId}/cancel`) });
  const jobs = view.jobs.slice(0, 6);

  return (
    <div className="max-h-40 overflow-y-auto px-3 py-2">
      <div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">{t("jobs.title")}</div>
      {jobs.length === 0 && <p className="text-xs text-muted-foreground">{t("jobs.empty")}</p>}
      <div className="space-y-1">
        {jobs.map((job) => (
          <div key={job.id} className="grid grid-cols-[120px_90px_minmax(0,1fr)_auto] items-center gap-2 text-xs">
            <span>{t(`jobs.kind.${job.kind}` as MessageKey)}</span>
            <Badge tone={TONE[job.status]}>{t(`jobs.status.${job.status}` as MessageKey)}</Badge>
            <div className="min-w-0">
              {job.status === "running" ? (
                <div className="flex items-center gap-2">
                  <Progress value={job.progress} className="w-32" />
                  <span className="truncate text-muted-foreground">{job.message}</span>
                </div>
              ) : (
                <span className={job.status === "failed" ? "truncate text-destructive" : "truncate text-muted-foreground"}>
                  {summary(job)}
                </span>
              )}
            </div>
            <div className="flex gap-0.5">
              {(job.status === "running" || job.status === "queued") && (
                <Button size="icon" variant="ghost" title={t("jobs.cancel")} onClick={() => cancel.mutate(job.id)}>
                  <Square className="h-3.5 w-3.5" />
                </Button>
              )}
              {job.log_path && (
                <Button
                  size="icon"
                  variant="ghost"
                  title={t("jobs.log")}
                  onClick={async () => setLog({ job, text: await api.text(`/api/jobs/${job.id}/log`) })}
                >
                  <FileText className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>
      {log && (
        <Dialog open onOpenChange={(open) => !open && setLog(null)}>
          <DialogContent title={`${t("jobs.log")}: ${t(`jobs.kind.${log.job.kind}` as MessageKey)}`} className="w-[min(900px,95vw)]">
            <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded bg-background p-2 text-[11px]">{log.text}</pre>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
