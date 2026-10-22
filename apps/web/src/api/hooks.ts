import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { t } from "@/i18n/ru";

import { ApiError, api } from "./client";
import type { Job, Meta, ShowView } from "./types";

export const keys = {
  shows: ["shows"] as const,
  show: (id: string) => ["show", id] as const,
  jobs: (id: string) => ["jobs", id] as const,
  compiled: (id: string) => ["compiled", id] as const,
  validation: (id: string) => ["validation", id] as const,
  exports: (id: string) => ["exports", id] as const,
  revisions: (id: string) => ["revisions", id] as const,
  plugins: ["plugins"] as const,
  meta: ["meta"] as const,
};

export function useMeta() {
  return useQuery({ queryKey: keys.meta, queryFn: () => api.get<Meta>("/api/meta"), staleTime: Infinity });
}

export function useShow(id: string) {
  return useQuery({ queryKey: keys.show(id), queryFn: () => api.get<ShowView>(`/api/shows/${id}`) });
}

/** Everything a show screen derives from the show: refetched together after a change. */
export function invalidateShow(client: ReturnType<typeof useQueryClient>, id: string) {
  for (const key of [keys.show(id), keys.compiled(id), keys.validation(id), keys.exports(id), keys.revisions(id)]) {
    void client.invalidateQueries({ queryKey: key });
  }
}

/**
 * An edit of the show: sends the revision it was based on; a 409 means somebody else changed
 * the show, so the data is reloaded and the user is told to repeat the edit.
 */
export function useEdit<A>(id: string, send: (args: A, base: number | undefined) => Promise<unknown>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: A) => send(args, client.getQueryData<ShowView>(keys.show(id))?.show.revision),
    onSuccess: (data) => {
      if (data && typeof data === "object" && "show" in data) {
        client.setQueryData(keys.show(id), data as ShowView);
      }
      invalidateShow(client, id);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        window.alert(t("error.conflict"));
        invalidateShow(client, id);
      } else {
        window.alert(`${t("error.generic")}: ${error instanceof Error ? error.message : String(error)}`);
      }
    },
  });
}

export function useRunJob(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (job: { kind: string; args?: Record<string, unknown> }) =>
      api.post<Job>(`/api/shows/${id}/jobs`, { kind: job.kind, args: job.args ?? {} }),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.show(id) }),
    onError: (error) => window.alert(`${t("error.generic")}: ${error instanceof Error ? error.message : error}`),
  });
}

/** Live updates (SSE): job progress patches the cache; a finished job reloads its show. */
export function useEvents() {
  const client = useQueryClient();
  useEffect(() => {
    const source = new EventSource("/api/events");
    source.addEventListener("job", (event) => {
      const job = JSON.parse((event as MessageEvent).data) as Job;
      client.setQueryData<ShowView>(keys.show(job.show_id), (view) => {
        if (!view) return view;
        const others = view.jobs.filter((j) => j.id !== job.id);
        return { ...view, jobs: [job, ...others].sort((a, b) => b.created_at - a.created_at) };
      });
      if (job.status !== "queued" && job.status !== "running") {
        invalidateShow(client, job.show_id);
      }
    });
    source.addEventListener("show", (event) => {
      const entry = JSON.parse((event as MessageEvent).data) as { id: string };
      void client.invalidateQueries({ queryKey: keys.show(entry.id) });
      void client.invalidateQueries({ queryKey: keys.shows });
    });
    return () => source.close();
  }, [client]);
}
