import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import { keys, notifyError, useMeta } from "@/api/hooks";
import type { ShowSummary, ShowView } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Card, CardContent, Field, Input, Select } from "@/components/ui/primitives";
import { plural, t } from "@/i18n/ru";
import { href } from "@/lib/router";
import { formatSeconds } from "@/lib/utils";

export function ShowsPage() {
  const shows = useQuery({ queryKey: keys.shows, queryFn: () => api.get<ShowSummary[]>("/api/shows") });
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t("shows.title")}</h1>
        <NewShowDialog />
      </div>
      {shows.isLoading && <p className="text-muted-foreground">{t("common.loading")}</p>}
      {shows.data?.length === 0 && <p className="text-muted-foreground">{t("shows.empty")}</p>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {shows.data?.map((show) => (
          <a key={show.id} href={href({ page: "show", id: show.id })}>
            <Card className="h-full transition-colors hover:border-primary/60">
              <CardContent>
                <div className="font-semibold">{show.title}</div>
                <div className="text-xs text-muted-foreground">{show.id}</div>
                <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                  <span>{plural(show.drones, "shows.drones")}</span>
                  <span>{formatSeconds(show.duration_s)}</span>
                  <span>{plural(show.scenes, "shows.scenes")}</span>
                  <span>{plural(show.models, "shows.models")}</span>
                  <span>
                    {t("shows.revision")} {show.revision}
                  </span>
                </div>
              </CardContent>
            </Card>
          </a>
        ))}
      </div>
    </div>
  );
}

function NewShowDialog() {
  const meta = useMeta();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ title: "", drones: 150, duration_s: 600, preset: "conservative", viewer: "-Y" });
  const create = useMutation({
    mutationFn: () => api.post<ShowView>("/api/shows", form),
    onSuccess: (view) => {
      void client.invalidateQueries({ queryKey: keys.shows });
      setOpen(false);
      window.location.hash = href({ page: "show", id: view.show.id });
    },
    onError: notifyError,
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4" /> {t("shows.new")}
        </Button>
      </DialogTrigger>
      <DialogContent title={t("shows.new")}>
        <form
          className="grid grid-cols-2 gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="col-span-2">
            <Field label={t("shows.form.title")}>
              <Input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </Field>
          </div>
          <Field label={t("shows.form.drones")}>
            <Input type="number" min={1} value={form.drones} onChange={(e) => setForm({ ...form, drones: Number(e.target.value) })} />
          </Field>
          <Field label={t("shows.form.duration")}>
            <Input type="number" min={1} value={form.duration_s} onChange={(e) => setForm({ ...form, duration_s: Number(e.target.value) })} />
          </Field>
          <Field label={t("shows.form.preset")}>
            <Select value={form.preset} onChange={(e) => setForm({ ...form, preset: e.target.value })}>
              {Object.keys(meta.data?.presets ?? { conservative: 1 }).map((p) => (
                <option key={p}>{p}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("shows.form.viewer")}>
            <Select value={form.viewer} onChange={(e) => setForm({ ...form, viewer: e.target.value })}>
              {["-Y", "+Y", "-X", "+X"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </Select>
          </Field>
          <div className="col-span-2 flex justify-end">
            <Button type="submit" disabled={create.isPending}>
              {t("shows.form.create")}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
