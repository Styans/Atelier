import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import { useEdit } from "@/api/hooks";
import type { AnimTrack, ComponentInfo, Easing, Region, Scene, Vec3 } from "@/api/types";
import { SchemaForm } from "@/components/SchemaForm";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/primitives";
import { t } from "@/i18n/ru";
import { notify } from "@/lib/feedback";

import { useShowState } from "./context";

const vecText = (v: Vec3) => v.join(", ");
function parseVec(text: string): Vec3 | null {
  const parts = text.split(/[,;\s]+/).filter(Boolean).map((p) => Number(p.replace(",", ".")));
  return parts.length === 3 && parts.every((p) => !Number.isNaN(p)) ? (parts as Vec3) : null;
}
const optionalNumber = (text: string): number | null => (text.trim() ? Number(text.replace(",", ".")) : null);

export function SceneEditor({ scene, onClose }: { scene: Scene | null; onClose: () => void }) {
  const { id, view } = useShowState();
  const models = Object.values(view.show.models);
  const primitives = useQuery({
    queryKey: ["components", "animation_primitive"],
    queryFn: () => api.get<ComponentInfo[]>("/api/components/animation_primitive"),
  });
  const [title, setTitle] = useState(scene?.title ?? "");
  const [modelId, setModelId] = useState(scene?.model_id ?? models[0]?.id ?? "");
  const [hold, setHold] = useState(String(scene?.hold_s ?? 15));
  const [transition, setTransition] = useState(scene?.transition_in.duration_s?.toString() ?? "");
  const [offset, setOffset] = useState(vecText(scene?.offset_m ?? [0, 0, 0]));
  const [rotation, setRotation] = useState(vecText(scene?.rotation_deg ?? [0, 0, 0]));
  const [tracks, setTracks] = useState<AnimTrack[]>(scene?.animation?.tracks ?? []);

  const save = useEdit(id, (body: Record<string, unknown>, base) =>
    scene
      ? api.patch(`/api/shows/${id}/scenes/${scene.id}`, { base_revision: base, changes: body })
      : api.post(`/api/shows/${id}/scenes`, { base_revision: base, ...body }),
  );

  const submit = () => {
    const offsetV = parseVec(offset);
    const rotationV = parseVec(rotation);
    if (!offsetV || !rotationV) {
      notify(t("scene.vectorHint"), "warning");
      return;
    }
    const holdS = Number(hold.replace(",", "."));
    const transitionS = optionalNumber(transition);
    if (scene) {
      save.mutate(
        {
          title: title || scene.title,
          model_id: modelId,
          hold_s: holdS,
          transition_in: { ...scene.transition_in, duration_s: transitionS },
          offset_m: offsetV,
          rotation_deg: rotationV,
          animation: tracks.length ? { source_prompt: scene.animation?.source_prompt ?? "", tracks, color_tracks: [] } : null,
        },
        { onSuccess: onClose },
      );
    } else {
      save.mutate(
        { model_id: modelId, hold_s: holdS, title: title || null, transition_s: transitionS, offset_m: offsetV, rotation_deg: rotationV, tracks },
        { onSuccess: onClose },
      );
    }
  };

  const updateTrack = (index: number, update: Partial<AnimTrack>) =>
    setTracks(tracks.map((tr, i) => (i === index ? { ...tr, ...update } : tr)));

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={scene ? `${t("scene.title")} ${scene.id}` : t("timeline.add")} className="w-[min(760px,95vw)]">
        <div className="grid grid-cols-2 gap-2">
          <Field label={t("scene.name")}>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label={t("scene.model")}>
            <Select value={modelId} onChange={(e) => setModelId(e.target.value)}>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("scene.hold")}>
            <Input value={hold} onChange={(e) => setHold(e.target.value)} />
          </Field>
          <Field label={t("scene.transition")}>
            <Input value={transition} onChange={(e) => setTransition(e.target.value)} />
          </Field>
          <Field label={t("scene.offset")}>
            <Input value={offset} onChange={(e) => setOffset(e.target.value)} />
          </Field>
          <Field label={t("scene.rotation")}>
            <Input value={rotation} onChange={(e) => setRotation(e.target.value)} />
          </Field>
        </div>

        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold">{t("scene.tracks")}</span>
            <Button
              size="sm"
              variant="outline"
              disabled={!primitives.data?.length}
              onClick={() =>
                setTracks([
                  ...tracks,
                  { primitive: primitives.data![0].id, region: { type: "all" }, start_s: 0, duration_s: null, easing: "ease_in_out", loop: false, params: {} },
                ])
              }
            >
              <Plus className="h-3.5 w-3.5" /> {t("scene.addTrack")}
            </Button>
          </div>
          {tracks.map((track, index) => {
            const component = primitives.data?.find((c) => c.id === track.primitive);
            return (
              <div key={index} className="space-y-2 rounded border p-2">
                <div className="grid grid-cols-[1fr_90px_140px_140px_auto] items-end gap-2">
                  <Field label={t("scene.track.primitive")}>
                    <Select value={track.primitive} onChange={(e) => updateTrack(index, { primitive: e.target.value, params: {} })}>
                      {primitives.data?.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.id}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label={t("scene.track.start")}>
                    <Input value={track.start_s} onChange={(e) => updateTrack(index, { start_s: Number(e.target.value.replace(",", ".")) || 0 })} />
                  </Field>
                  <Field label={t("scene.track.duration")}>
                    <Input value={track.duration_s ?? ""} onChange={(e) => updateTrack(index, { duration_s: optionalNumber(e.target.value) })} />
                  </Field>
                  <Field label={t("scene.track.easing")}>
                    <Select value={track.easing} onChange={(e) => updateTrack(index, { easing: e.target.value as Easing })}>
                      <option value="linear">linear</option>
                      <option value="ease_in_out">ease_in_out</option>
                      <option value="sine">sine</option>
                    </Select>
                  </Field>
                  <Button size="icon" variant="ghost" onClick={() => setTracks(tracks.filter((_, i) => i !== index))} title={t("common.delete")}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                {component && <p className="text-[11px] text-muted-foreground">{component.description}</p>}
                {component && <SchemaForm key={track.primitive} schema={component.params_schema} value={track.params} onChange={(params) => updateTrack(index, { params })} />}
                <RegionEditor region={track.region} onChange={(region) => updateTrack(index, { region })} />
              </div>
            );
          })}
        </div>

        <div className="mt-3 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button onClick={submit} disabled={save.isPending || !modelId}>
            {t("common.save")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RegionEditor({ region, onChange }: { region: Region; onChange: (r: Region) => void }) {
  const [a, setA] = useState(region.type === "plane_split" ? vecText(region.normal) : region.type === "box" ? vecText(region.min) : "1, 0, 0");
  const [b, setB] = useState(region.type === "box" ? vecText(region.max) : "");
  return (
    <div className="grid grid-cols-[140px_1fr_1fr_110px] items-end gap-2">
      <Field label={t("scene.track.region")}>
        <Select
          value={region.type}
          onChange={(e) => {
            const type = e.target.value;
            if (type === "all") onChange({ type: "all" });
            if (type === "plane_split") onChange({ type: "plane_split", normal: parseVec(a) ?? [1, 0, 0], offset_m: 0, side: "positive" });
            if (type === "box") onChange({ type: "box", min: [-1, -1, -1], max: [1, 1, 1] });
          }}
        >
          <option value="all">{t("scene.region.all")}</option>
          <option value="plane_split">{t("scene.region.plane")}</option>
          <option value="box">{t("scene.region.box")}</option>
        </Select>
      </Field>
      {region.type === "plane_split" && (
        <>
          <Field label={t("scene.region.normal")}>
            <Input
              value={a}
              onChange={(e) => {
                setA(e.target.value);
                const v = parseVec(e.target.value);
                if (v) onChange({ ...region, normal: v });
              }}
            />
          </Field>
          <Field label={t("scene.region.offset")}>
            <Input value={region.offset_m} onChange={(e) => onChange({ ...region, offset_m: Number(e.target.value.replace(",", ".")) || 0 })} />
          </Field>
          <Field label={t("scene.region.side")}>
            <Select value={region.side} onChange={(e) => onChange({ ...region, side: e.target.value as "positive" | "negative" })}>
              <option value="positive">+</option>
              <option value="negative">−</option>
            </Select>
          </Field>
        </>
      )}
      {region.type === "box" && (
        <>
          <Field label={t("scene.region.min")}>
            <Input
              value={region.type === "box" && a === "1, 0, 0" ? vecText(region.min) : a}
              onChange={(e) => {
                setA(e.target.value);
                const v = parseVec(e.target.value);
                if (v) onChange({ ...region, min: v });
              }}
            />
          </Field>
          <Field label={t("scene.region.max")}>
            <Input
              value={b || vecText(region.max)}
              onChange={(e) => {
                setB(e.target.value);
                const v = parseVec(e.target.value);
                if (v) onChange({ ...region, max: v });
              }}
            />
          </Field>
        </>
      )}
    </div>
  );
}
