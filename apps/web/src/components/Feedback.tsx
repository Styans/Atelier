import { X } from "lucide-react";
import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { t } from "@/i18n/ru";
import { answer, dismiss, snapshot, subscribe } from "@/lib/feedback";
import { cn } from "@/lib/utils";

const TONE = {
  error: "border-destructive/60 bg-destructive/15",
  warning: "border-warning/60 bg-warning/15",
  info: "border-primary/40 bg-card",
} as const;

/** The confirm dialog and the notices of lib/feedback; mounted once in App. */
export function Feedback() {
  const { confirm, toasts } = useSyncExternalStore(subscribe, snapshot);
  return (
    <>
      {confirm && (
        <Dialog key={confirm.id} open onOpenChange={(open) => !open && answer(false)}>
          <DialogContent title={confirm.title} className="w-[min(460px,95vw)]">
            {confirm.message && <p className="whitespace-pre-line text-sm text-muted-foreground">{confirm.message}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={() => answer(false)}>
                {t("common.cancel")}
              </Button>
              <Button variant={confirm.destructive ? "destructive" : "default"} onClick={() => answer(true)} autoFocus>
                {confirm.confirmLabel ?? t("common.confirm")}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      )}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(420px,90vw)] flex-col gap-2" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} role="status" className={cn("pointer-events-auto flex items-start gap-2 rounded border px-3 py-2 text-sm shadow-lg", TONE[toast.tone])}>
            <span className="flex-1 whitespace-pre-line break-words">{toast.message}</span>
            <button className="rounded p-0.5 hover:bg-accent" onClick={() => dismiss(toast.id)} title={t("common.close")}>
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
