import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronRight, RotateCcw, Send, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/api/client";
import { invalidateShow, notifyError } from "@/api/hooks";
import type { ChatEvent, ChatState, Job, Proposal } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/primitives";
import { t, tOr } from "@/i18n/ru";
import { confirmAction } from "@/lib/feedback";
import { cn } from "@/lib/utils";
import { useShowState } from "@/show/context";

import { type Live, type ToolCall, applyEvent, buildTurns, canRollback, emptyLive } from "./thread";

const EVENT_KINDS: ChatEvent["kind"][] = ["status", "text", "tool_start", "tool_result", "done", "error"];

function ToolChip({ tool }: { tool: ToolCall }) {
  const [open, setOpen] = useState(false);
  const pending = tool.result === undefined;
  return (
    <div className={cn("rounded border text-xs", tool.isError ? "border-destructive/50" : "border-border")}>
      <button className="flex w-full items-center gap-1 px-2 py-1 text-left" onClick={() => setOpen(!open)}>
        <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
        <span className="font-medium">{tOr(`chat.tool.${tool.name}`, tool.name)}</span>
        <span className={cn("ml-auto truncate", tool.isError ? "text-destructive" : "text-muted-foreground")}>
          {pending ? t("chat.tool.running") : tool.isError ? t("chat.tool.failed") : t("chat.tool.done")}
        </span>
      </button>
      {open && (
        <div className="space-y-1 border-t px-2 py-1">
          {tool.result && <p className="whitespace-pre-wrap">{tool.result}</p>}
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-[10px] text-muted-foreground">
            {JSON.stringify(tool.input, null, 1)}
          </pre>
        </div>
      )}
    </div>
  );
}

function Reply({ text, tools }: { text: string; tools: ToolCall[] }) {
  return (
    <div className="space-y-1">
      {text && <p className="whitespace-pre-wrap text-sm">{text}</p>}
      {tools.map((tool) => (
        <ToolChip key={tool.id} tool={tool} />
      ))}
    </div>
  );
}

function ProposalCard({ proposal }: { proposal: Proposal }) {
  const { id, view } = useShowState();
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: (decision: "apply" | "dismiss") =>
      api.post(`/api/shows/${id}/proposals/${proposal.id}/${decision}`, { base_revision: view.show.revision }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["chat", id] });
      invalidateShow(client, id);
    },
    onError: notifyError,
  });
  return (
    <div className="space-y-2 rounded border border-primary/40 bg-primary/5 p-2 text-sm">
      <div className="text-xs font-semibold text-primary">
        {t("chat.proposal")}: {tOr(`chat.action.${proposal.action}`, proposal.action)}
      </div>
      <p>{proposal.summary}</p>
      <div className="flex gap-2">
        <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate("apply")}>
          <Check className="h-3.5 w-3.5" /> {t("chat.apply")}
        </Button>
        <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate("dismiss")}>
          <X className="h-3.5 w-3.5" /> {t("chat.dismiss")}
        </Button>
      </div>
    </div>
  );
}

/** Which other job a queued turn waits for (one worker runs everything in order). */
function waitingFor(turn: Job | null, jobs: Job[]): string | null {
  if (!turn || turn.status !== "queued") return null;
  const running = jobs.find((j) => j.status === "running" && j.id !== turn.id);
  return running ? tOr(`jobs.kind.${running.kind}`, running.kind) : null;
}

export function ChatPanel() {
  const { id, view } = useShowState();
  const client = useQueryClient();
  const chat = useQuery({ queryKey: ["chat", id], queryFn: () => api.get<ChatState>(`/api/shows/${id}/chat`) });
  const [draft, setDraft] = useState("");
  const [turnId, setTurnId] = useState<string | null>(null);
  const [live, setLive] = useState<Live>(emptyLive);
  const bottom = useRef<HTMLDivElement>(null);
  const activeTurn = chat.data?.active_turn ?? null;
  const streaming = turnId ?? activeTurn?.id ?? null;

  const send = useMutation({
    mutationFn: (text: string) => api.post<{ job: Job }>(`/api/shows/${id}/chat`, { text }),
    onSuccess: (data) => {
      setDraft("");
      setLive(emptyLive);
      setTurnId(data.job.id);
      void client.invalidateQueries({ queryKey: ["chat", id] });
    },
    onError: notifyError,
  });
  const rollback = useMutation({
    mutationFn: (turn: string) => api.post(`/api/shows/${id}/chat/${turn}/rollback`, { base_revision: view.show.revision }),
    onSuccess: () => invalidateShow(client, id),
    onError: notifyError,
  });
  const cancel = useMutation({ mutationFn: (job: string) => api.post(`/api/jobs/${job}/cancel`) });

  useEffect(() => {
    if (!streaming) return;
    const source = new EventSource(`/api/shows/${id}/chat/events?turn=${streaming}`);
    const finish = () => {
      source.close();
      setTurnId(null);
      setLive(emptyLive);
      void client.invalidateQueries({ queryKey: ["chat", id] });
      invalidateShow(client, id);
    };
    for (const kind of EVENT_KINDS) {
      source.addEventListener(kind, (e) => {
        const event = JSON.parse((e as MessageEvent).data) as ChatEvent;
        setLive((current) => applyEvent(current, event));
        if (kind === "done" || kind === "error") finish();
        else if (kind === "tool_result") invalidateShow(client, id); // the show changed: refresh it
      });
    }
    source.addEventListener("end", finish);
    return () => source.close();
  }, [streaming, id, client]);

  useEffect(() => {
    // In braces: newer browsers return a Promise from scrollIntoView, and an effect may only
    // return its clean-up function.
    void bottom.current?.scrollIntoView({ block: "end" });
  }, [chat.data, live]);

  const turns = buildTurns(chat.data?.messages ?? []);
  const pending = (chat.data?.proposals ?? []).filter((p) => p.status === "pending");
  const waiting = waitingFor(activeTurn, view.jobs);
  const busy = Boolean(streaming) || send.isPending;
  const submit = () => draft.trim() && !busy && send.mutate(draft.trim());

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {turns.length === 0 && !busy && <p className="text-sm text-muted-foreground">{t("chat.empty")}</p>}
        {turns.map((turn, index) => (
          <div key={turn.id ?? `t${index}`} className="space-y-2">
            {turn.items.map((item) =>
              item.kind === "user" ? (
                <div key={item.key} className="ml-6 whitespace-pre-wrap rounded-lg bg-accent px-3 py-2 text-sm">
                  {item.text}
                </div>
              ) : item.kind === "notice" ? (
                <div key={item.key} className="rounded border border-warning/50 px-2 py-1 text-xs text-warning">
                  {item.text}
                </div>
              ) : (
                <Reply key={item.key} text={item.text} tools={item.tools} />
              ),
            )}
            {canRollback(turn) && turn.id !== streaming && (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-1 text-xs text-muted-foreground"
                onClick={async () => {
                  const ok = await confirmAction({
                    title: t("chat.rollback.title"),
                    message: t("chat.rollback.confirm", { n: turn.revisionBefore ?? 0 }),
                    confirmLabel: t("chat.rollback"),
                  });
                  if (ok && turn.id) rollback.mutate(turn.id);
                }}
              >
                <RotateCcw className="h-3 w-3" /> {t("chat.rollback")}
              </Button>
            )}
          </div>
        ))}
        {streaming && (
          <div className="space-y-2">
            {live.replies.map((reply, i) => (
              <Reply key={i} text={reply.text} tools={reply.tools} />
            ))}
            <Reply text={live.text} tools={live.tools} />
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span className="animate-pulse">
                {waiting
                  ? t("chat.waiting", { job: waiting })
                  : tOr(`chat.stage.${live.stage ?? "queued"}`, t("chat.stage.queued"))}
              </span>
              <Button size="icon" variant="ghost" className="h-6 w-6" title={t("jobs.cancel")} onClick={() => cancel.mutate(streaming)}>
                <Square className="h-3 w-3" />
              </Button>
            </div>
          </div>
        )}
        {pending.map((proposal) => (
          <ProposalCard key={proposal.id} proposal={proposal} />
        ))}
        <div ref={bottom} />
      </div>
      <div className="border-t p-2">
        <div className="flex gap-2">
          <Textarea
            value={draft}
            rows={3}
            placeholder={t("chat.placeholder")}
            className="min-h-0 flex-1 resize-none"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
          />
          <Button size="icon" title={t("chat.send")} disabled={busy || !draft.trim()} onClick={submit}>
            <Send className="h-4 w-4" />
          </Button>
        </div>
        <p className="mt-1 text-[10px] text-muted-foreground">{t("chat.hint")}</p>
      </div>
    </div>
  );
}
