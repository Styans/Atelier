import { useQuery } from "@tanstack/react-query";
import { Box, Eye, Image as ImageIcon, Music, Pencil, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { api } from "@/api/client";
import { useEdit, useMeta, useRunJob } from "@/api/hooks";
import type { Brief, ModelCard } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Badge, Card, CardContent, CardHeader, CardTitle, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { t } from "@/i18n/ru";
import { cn, formatSeconds } from "@/lib/utils";

import { staleReason, useShowState } from "./context";

export function Materials() {
  return (
    <div className="space-y-3">
      <BriefCard />
      <ModelsCard />
      <MusicCard />
    </div>
  );
}

function BriefCard() {
  const { id, view } = useShowState();
  const meta = useMeta();
  const brief = view.show.brief;
  const [draft, setDraft] = useState<Partial<Brief>>({});
  const [palette, setPalette] = useState<string | null>(null);
  const save = useEdit(id, (changes: Partial<Brief>, base) =>
    api.patch(`/api/shows/${id}/brief`, { base_revision: base, changes }),
  );
  const fleet = useEdit(id, (body: { preset?: string; values?: Record<string, number> }, base) =>
    api.put(`/api/shows/${id}/fleet`, { base_revision: base, ...body }),
  );
  const value = <K extends keyof Brief>(key: K): Brief[K] => (key in draft ? (draft[key] as Brief[K]) : brief[key]);
  const changes: Partial<Brief> = { ...draft };
  if (palette !== null) changes.palette = palette.split(/[,\s]+/).filter(Boolean);
  const dirty = Object.keys(changes).length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("brief.title")}</CardTitle>
        {brief.status === "approved" && <Badge tone="ok">{t("stage.approved")}</Badge>}
      </CardHeader>
      <CardContent>
        <Field label={t("brief.name")}>
          <Input value={value("title")} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
        </Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label={t("brief.occasion")}>
            <Input value={value("occasion") ?? ""} onChange={(e) => setDraft({ ...draft, occasion: e.target.value || null })} />
          </Field>
          <Field label={t("brief.location")}>
            <Input value={value("location") ?? ""} onChange={(e) => setDraft({ ...draft, location: e.target.value || null })} />
          </Field>
          <Field label={t("brief.duration")}>
            <Input type="number" value={value("duration_s")} onChange={(e) => setDraft({ ...draft, duration_s: Number(e.target.value) })} />
          </Field>
          <Field label={t("brief.viewer")}>
            <Select value={value("viewer_direction")} onChange={(e) => setDraft({ ...draft, viewer_direction: e.target.value as Brief["viewer_direction"] })}>
              {["-Y", "+Y", "-X", "+X"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label={t("brief.mood")}>
          <Input value={value("mood") ?? ""} onChange={(e) => setDraft({ ...draft, mood: e.target.value || null })} />
        </Field>
        <Field label={t("brief.palette")}>
          <Input value={palette ?? brief.palette.join(", ")} onChange={(e) => setPalette(e.target.value)} />
        </Field>
        <Field label={t("brief.notes")}>
          <Textarea value={value("notes") ?? ""} onChange={(e) => setDraft({ ...draft, notes: e.target.value || null })} />
        </Field>
        {dirty && (
          <Button
            size="sm"
            onClick={() =>
              save.mutate(changes, {
                onSuccess: () => {
                  setDraft({});
                  setPalette(null);
                },
              })
            }
          >
            {t("common.save")}
          </Button>
        )}
        <div className="border-t pt-2">
          <div className="mb-1 text-xs text-muted-foreground">{t("brief.fleet")}</div>
          <div className="grid grid-cols-2 gap-2">
            <Field label={t("brief.preset")}>
              <Select value={brief.fleet.preset ?? ""} onChange={(e) => e.target.value && fleet.mutate({ preset: e.target.value })}>
                {!brief.fleet.preset && <option value="">custom</option>}
                {Object.keys(meta.data?.presets ?? {}).map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </Select>
            </Field>
            <Field label={t("brief.drones")}>
              <Input
                type="number"
                defaultValue={brief.fleet.drone_count}
                onBlur={(e) => {
                  const n = Number(e.target.value);
                  if (n > 0 && n !== brief.fleet.drone_count) fleet.mutate({ values: { drone_count: n } });
                }}
              />
            </Field>
          </div>
          <p className="text-[11px] text-muted-foreground">
            d ≥ {brief.fleet.min_distance_m} м · v ≤ {brief.fleet.max_speed_h_ms}/{brief.fleet.max_speed_up_ms}/{brief.fleet.max_speed_down_ms} м/с · a ≤{" "}
            {brief.fleet.max_accel_ms2} м/с² · {brief.fleet.altitude_min_m}–{brief.fleet.altitude_max_m} м
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function ModelsCard() {
  const { id, view, setViewer, setTab } = useShowState();
  const fileRef = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState<ModelCard | null>(null);
  const drones = view.show.brief.fleet.drone_count;
  const runJob = useRunJob(id);
  const upload = useEdit(id, (file: File, base) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", file.name.replace(/\.[^.]+$/, ""));
    if (base !== undefined) form.append("base_revision", String(base));
    return api.upload(`/api/shows/${id}/models`, form);
  });
  const models = Object.values(view.show.models);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("models.title")}</CardTitle>
        <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={upload.isPending} title={t("models.upload")}>
          <Upload className="h-3.5 w-3.5" />
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".glb,.gltf,.obj,.stl,.ply"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload.mutate(file);
            e.target.value = "";
          }}
        />
      </CardHeader>
      <CardContent>
        {models.length === 0 && <p className="text-xs text-muted-foreground">{t("models.empty")}</p>}
        {models.map((card) => {
          const n = card.drone_count ?? drones;
          const pointsSlot = `models.${card.id}.points.${n}`;
          const reason = staleReason(view, pointsSlot);
          return (
            <div key={card.id} className="rounded border p-2">
              <div className="flex items-center justify-between gap-1">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{card.name}</div>
                  <div className="text-[11px] text-muted-foreground">{card.id}</div>
                </div>
                <div className="flex gap-0.5">
                  <Button
                    size="icon"
                    variant="ghost"
                    title={t("models.view")}
                    disabled={!card.assets.mesh}
                    onClick={() => {
                      setViewer({ mode: card.assets.point_sets[n] ? "points" : "model", modelId: card.id });
                      setTab("viewer");
                    }}
                  >
                    <Eye className="h-4 w-4" />
                  </Button>
                  <Button size="icon" variant="ghost" title={t("common.edit")} onClick={() => setEditing(card)}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1">
                {!card.assets.mesh && <Badge tone="warning">{t("models.noMesh")}</Badge>}
                {reason === undefined ? (
                  card.assets.mesh && (
                    <Button size="sm" variant="secondary" onClick={() => runJob.mutate({ kind: "points", args: { model_id: card.id } })}>
                      {t("models.computePoints")}
                    </Button>
                  )
                ) : reason === null ? (
                  <Badge tone="ok">
                    {t("models.points")} {n}
                  </Badge>
                ) : (
                  <>
                    <Badge tone="warning" title={reason}>
                      {t("models.points")}: {t("stage.stale")}
                    </Badge>
                    <Button size="sm" variant="secondary" onClick={() => runJob.mutate({ kind: "points", args: { model_id: card.id } })}>
                      {t("models.computePoints")}
                    </Button>
                  </>
                )}
                {card.target_height_m && <Badge>{card.target_height_m} м</Badge>}
              </div>
              <Generation card={card} />
            </div>
          );
        })}
        {editing && <ModelDialog card={editing} onClose={() => setEditing(null)} />}
      </CardContent>
    </Card>
  );
}

/** Concepts from the card's prompt, then a 3D model from the chosen one (GPU jobs, by click). */
function Generation({ card }: { card: ModelCard }) {
  const { id, view } = useShowState();
  const runJob = useRunJob(id);
  const concepts = useQuery({
    queryKey: ["concepts", id, card.id, card.assets.concepts.length],
    queryFn: () => api.get<string[]>(`/api/shows/${id}/models/${card.id}/concepts`),
    enabled: card.assets.concepts.length > 0,
  });
  const [picked, setPicked] = useState<string | null>(null);
  const chosen = picked ?? card.assets.selected_concept ?? concepts.data?.[0] ?? null;
  const prompt = card.image_prompt || card.description || card.name;
  const src = (concept: string) => {
    const [artifact, file] = concept.split("/");
    const record = view.show.artifacts[artifact];
    return record ? `/api/shows/${id}/files/${record.path}/${file}` : undefined;
  };
  return (
    <div className="mt-1 space-y-1">
      <div className="flex flex-wrap gap-1">
        <Button
          size="sm"
          variant="outline"
          title={prompt}
          disabled={runJob.isPending}
          onClick={() => runJob.mutate({ kind: "generate_images", args: { model_id: card.id, prompt, n: 4 } })}
        >
          <ImageIcon className="h-3.5 w-3.5" /> {card.assets.concepts.length ? t("models.moreConcepts") : t("models.concepts")}
        </Button>
        {chosen && (
          <Button
            size="sm"
            variant="outline"
            disabled={runJob.isPending}
            onClick={() => runJob.mutate({ kind: "generate_mesh", args: { model_id: card.id, concept: chosen } })}
          >
            <Box className="h-3.5 w-3.5" /> {card.assets.mesh ? t("models.remesh") : t("models.mesh")}
          </Button>
        )}
      </div>
      {concepts.data && concepts.data.length > 0 && (
        <div className="grid grid-cols-4 gap-1">
          {concepts.data.slice(0, 8).map((concept) => (
            <button
              key={concept}
              type="button"
              title={t("models.pickConcept")}
              onClick={() => setPicked(concept)}
              className={cn("overflow-hidden rounded border", concept === chosen ? "ring-2 ring-primary" : "opacity-70 hover:opacity-100")}
            >
              <img src={src(concept)} alt="" className="aspect-square w-full object-cover" loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ModelDialog({ card, onClose }: { card: ModelCard; onClose: () => void }) {
  const { id } = useShowState();
  const [name, setName] = useState(card.name);
  const [height, setHeight] = useState(card.target_height_m?.toString() ?? "");
  const [color, setColor] = useState(card.color_source);
  const [palette, setPalette] = useState(card.palette.join(", "));
  const [prompt, setPrompt] = useState(card.image_prompt ?? "");
  const save = useEdit(id, (changes: Record<string, unknown>, base) =>
    api.patch(`/api/shows/${id}/models/${card.id}`, { base_revision: base, changes }),
  );
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={card.name}>
        <div className="space-y-2">
          <Field label={t("brief.name")}>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label={t("models.height")}>
            <Input value={height} onChange={(e) => setHeight(e.target.value)} />
          </Field>
          <Field label={t("models.color")}>
            <Select value={color} onChange={(e) => setColor(e.target.value as ModelCard["color_source"])}>
              <option value="texture">{t("models.color.texture")}</option>
              <option value="palette">{t("models.color.palette")}</option>
              <option value="solid">{t("models.color.solid")}</option>
            </Select>
          </Field>
          <Field label={t("brief.palette")}>
            <Input value={palette} onChange={(e) => setPalette(e.target.value)} />
          </Field>
          <Field label={t("models.prompt")}>
            <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() =>
                save.mutate(
                  {
                    name,
                    target_height_m: height.trim() ? Number(height.replace(",", ".")) : null,
                    color_source: color,
                    palette: palette.split(/[,\s]+/).filter(Boolean),
                    image_prompt: prompt.trim() || null,
                  },
                  { onSuccess: onClose },
                )
              }
            >
              {t("common.save")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function MusicCard() {
  const { id, view } = useShowState();
  const audio = view.show.audio;
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useEdit(id, (file: File, base) => {
    const form = new FormData();
    form.append("file", file);
    if (base !== undefined) form.append("base_revision", String(base));
    return api.upload(`/api/shows/${id}/music`, form);
  });
  const offset = useEdit(id, (value: number | null, base) =>
    api.patch(`/api/shows/${id}/music`, { base_revision: base, offset_s: value }),
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1">
          <Music className="h-4 w-4" /> {t("music.title")}
        </CardTitle>
        <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={upload.isPending} title={t("music.upload")}>
          <Upload className="h-3.5 w-3.5" />
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".mp3,.wav,.flac,.ogg"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload.mutate(file);
            e.target.value = "";
          }}
        />
      </CardHeader>
      <CardContent>
        {!audio && <p className="text-xs text-muted-foreground">{t("music.none")}</p>}
        {audio && (
          <>
            <div className="text-sm">
              {audio.asset.original_name} · {formatSeconds(audio.duration_s)}
            </div>
            <Field label={t("music.offset")} hint={audio.asset.original_name.toLowerCase().endsWith(".mp3") ? undefined : t("music.mp3")}>
              <Input
                defaultValue={audio.offset_s ?? ""}
                onBlur={(e) => {
                  const text = e.target.value.trim();
                  const value = text ? Number(text.replace(",", ".")) : null;
                  if (value !== audio.offset_s) offset.mutate(value);
                }}
              />
            </Field>
          </>
        )}
      </CardContent>
    </Card>
  );
}
