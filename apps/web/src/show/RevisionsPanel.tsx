import { useQuery } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import { keys, useEdit } from "@/api/hooks";
import type { DiffEntry, RevisionInfo } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/primitives";
import { t } from "@/i18n/ru";
import { confirmAction } from "@/lib/feedback";
import { cn } from "@/lib/utils";

import { useShowState } from "./context";

export function RevisionsPanel() {
  const { id, view } = useShowState();
  const [selected, setSelected] = useState<number | null>(null);
  const revisions = useQuery({ queryKey: keys.revisions(id), queryFn: () => api.get<RevisionInfo[]>(`/api/shows/${id}/revisions`) });
  const diff = useQuery({
    queryKey: ["diff", id, selected, view.show.revision],
    queryFn: () => api.get<DiffEntry[]>(`/api/shows/${id}/revisions/${selected}/diff`),
    enabled: selected !== null,
  });
  const rollback = useEdit(id, (revision: number, base) => api.post(`/api/shows/${id}/rollback/${revision}`, { base_revision: base }));
  const list = [...(revisions.data ?? [])].reverse();

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-3">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold">{t("revisions.title")}</h2>
        {list.map((r) => (
          <button
            key={r.revision}
            className={cn("block w-full rounded border px-2 py-1 text-left text-xs hover:bg-accent", selected === r.revision && "border-primary")}
            onClick={() => setSelected(r.revision)}
          >
            <div className="flex items-center gap-1">
              <b>#{r.revision}</b>
              <Badge>{r.author}</Badge>
              <span className="text-muted-foreground">{new Date(r.created_at).toLocaleString("ru-RU")}</span>
            </div>
            <div className="truncate">{r.message}</div>
          </button>
        ))}
      </div>
      <div className="space-y-2">
        {selected !== null && (
          <>
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold">
                #{selected}: {t("revisions.diff")}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={selected === view.show.revision}
                onClick={async () => {
                  const ok = await confirmAction({
                    title: t("revisions.rollback.title", { n: selected }),
                    message: t("revisions.rollback.confirm", { n: selected }),
                    confirmLabel: t("revisions.rollback"),
                  });
                  if (ok) rollback.mutate(selected);
                }}
              >
                <RotateCcw className="h-3.5 w-3.5" /> {t("revisions.rollback")}
              </Button>
            </div>
            <div className="space-y-0.5 text-xs">
              {diff.data?.length === 0 && <p className="text-muted-foreground">—</p>}
              {diff.data?.map((d, i) => (
                <div key={i} className="rounded bg-background px-2 py-1">
                  <div className="font-mono text-primary">{d.path}</div>
                  {d.old === null ? (
                    <div className="text-success">
                      + {t("revisions.added")}: {d.new}
                    </div>
                  ) : d.new === null ? (
                    <div className="text-destructive">
                      − {t("revisions.removed")}: {d.old}
                    </div>
                  ) : (
                    <div>
                      <span className="text-destructive line-through">{d.old}</span> → <span className="text-success">{d.new}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
