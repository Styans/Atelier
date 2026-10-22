import { Bounds, Grid, Html, OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useQuery } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import { unpack } from "@/api/binary";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { CompiledSummary, ValidationReport } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/primitives";
import { t } from "@/i18n/ru";
import { cn, formatSeconds } from "@/lib/utils";

import { type ViewerMode, useShowState } from "./context";
import { heatColor, interpolate } from "./playback";

// Show coordinates are Z-up; three.js is Y-up. Rotating -90 degrees about X maps
// show (x, y, z) to three (x, z, -y): the audience at show -Y sits in front of the camera.
const SHOW_TO_THREE: [number, number, number] = [-Math.PI / 2, 0, 0];

function dotTexture(): THREE.Texture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d")!;
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.9)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(canvas);
}

export function Viewer() {
  const { viewer, setViewer, view } = useShowState();
  const model = viewer.modelId ? view.show.models[viewer.modelId] : null;
  const modes: ViewerMode[] = ["model", "points", "show"];
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-1 border-b px-2 py-1">
        {modes.map((mode) => (
          <Button key={mode} size="sm" variant={viewer.mode === mode ? "secondary" : "ghost"} onClick={() => setViewer({ mode })}>
            {t(`viewer.mode.${mode}`)}
          </Button>
        ))}
        {viewer.mode !== "show" && model && <span className="ml-2 text-xs text-muted-foreground">{model.name}</span>}
      </div>
      {viewer.mode === "show" ? <ShowPlayer /> : <ModelStage />}
    </div>
  );
}

function Stage({ children, camera = [0, 30, 90] }: { children: React.ReactNode; camera?: [number, number, number] }) {
  return (
    <Canvas camera={{ position: camera, fov: 45, near: 0.1, far: 5000 }} className="bg-[#05070b]">
      <ambientLight intensity={0.8} />
      <directionalLight position={[30, 60, 40]} intensity={1.2} />
      <Grid args={[400, 400]} cellSize={3} sectionSize={30} infiniteGrid fadeDistance={300} cellColor="#1d2533" sectionColor="#2c3a52" />
      <Html position={[0, 0.2, 40]} center className="pointer-events-none select-none text-[11px] text-muted-foreground">
        ▼ {t("viewer.audience")}
      </Html>
      <OrbitControls makeDefault target={[0, 20, 0]} />
      {children}
    </Canvas>
  );
}

function ModelStage() {
  const { id, view, viewer } = useShowState();
  const model = viewer.modelId ? view.show.models[viewer.modelId] : null;
  const [heatmap, setHeatmap] = useState(true);
  if (!model) return <p className="p-6 text-muted-foreground">{t("viewer.pickModel")}</p>;
  if (viewer.mode === "model") {
    if (!model.assets.mesh) return <p className="p-6 text-muted-foreground">{t("models.noMesh")}</p>;
    const url = `/api/shows/${id}/models/${model.id}/mesh.glb?v=${model.assets.mesh}`;
    return (
      <Stage camera={[0, 2, 6]}>
        <Suspense fallback={null}>
          <Bounds fit clip observe margin={1.3}>
            <group rotation={SHOW_TO_THREE}>
              <Mesh url={url} />
            </group>
          </Bounds>
        </Suspense>
      </Stage>
    );
  }
  const n = model.drone_count ?? view.show.brief.fleet.drone_count;
  const pointsId = model.assets.point_sets[String(n)];
  if (!pointsId) return <p className="p-6 text-muted-foreground">{t("models.computePoints")}…</p>;
  return (
    <div className="relative min-h-0 flex-1">
      <PointsStage showId={id} modelId={model.id} version={pointsId} heatmap={heatmap} />
      <label className="absolute right-3 top-3 flex items-center gap-1 rounded bg-card/80 px-2 py-1 text-xs">
        <input type="checkbox" checked={heatmap} onChange={(e) => setHeatmap(e.target.checked)} /> {t("viewer.heatmap")}
      </label>
    </div>
  );
}

function Mesh({ url }: { url: string }) {
  const gltf = useGLTF(url);
  // The GLB carries show coordinates (Z-up) as they are; the parent group turns them for three.js.
  return <primitive object={gltf.scene.clone()} />;
}

interface PointsHeader {
  scale: number;
  min_distance_m: number;
  stale: string | null;
  metrics: Record<string, number>;
}

function PointsStage({ showId, modelId, version, heatmap }: { showId: string; modelId: string; version: string; heatmap: boolean }) {
  const data = useQuery({
    queryKey: ["points", showId, modelId, version],
    queryFn: async () => unpack<PointsHeader>(await api.binary(`/api/shows/${showId}/models/${modelId}/points`)),
  });
  const texture = useMemo(dotTexture, []);
  const geometry = useMemo(() => {
    if (!data.data) return null;
    const { arrays, header } = data.data;
    const positions = arrays.positions as Float32Array;
    const nearest = arrays.nearest as Float32Array;
    const own = arrays.colors as Uint8Array;
    const colors = new Float32Array(positions.length);
    for (let i = 0; i < nearest.length; i++) {
      const c = heatmap ? heatColor(nearest[i], header.min_distance_m) : [own[i * 3] / 255, own[i * 3 + 1] / 255, own[i * 3 + 2] / 255];
      colors.set(c, i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    return g;
  }, [data.data, heatmap]);
  const header = data.data?.header;
  const nearest = data.data?.arrays.nearest as Float32Array | undefined;
  const minSpacing = nearest ? Math.min(...nearest) : null;
  return (
    <>
      <Stage>
        {geometry && (
          <Bounds fit clip margin={1.4}>
            <group rotation={SHOW_TO_THREE}>
              <points geometry={geometry}>
                <pointsMaterial size={1.4} map={texture} vertexColors transparent depthWrite={false} sizeAttenuation />
              </points>
            </group>
          </Bounds>
        )}
      </Stage>
      {header && minSpacing !== null && (
        <div className="absolute left-3 top-3 rounded bg-card/80 px-2 py-1 text-xs">
          <div className={cn(minSpacing < header.min_distance_m && "text-destructive")}>
            {t("viewer.minSpacing")}: {minSpacing.toFixed(2)} м (≥ {header.min_distance_m} м)
          </div>
          <div className="text-muted-foreground">
            {t("viewer.scale")} × {header.scale.toFixed(2)}
            {header.metrics.cv_nn !== undefined && ` · CV ${header.metrics.cv_nn.toFixed(3)}`}
          </div>
          {header.stale && <div className="text-warning">{header.stale}</div>}
        </div>
      )}
    </>
  );
}

interface FramesHeader {
  drones: number;
  duration_s: number;
}

function ShowPlayer() {
  const { id, view, viewer, setViewer } = useShowState();
  const compiledId = view.show.outputs.compiled;
  const summary = useQuery({
    queryKey: keys.compiled(id),
    queryFn: () => api.get<CompiledSummary>(`/api/shows/${id}/compiled`),
    enabled: Boolean(compiledId),
  });
  const frames = useQuery({
    queryKey: ["frames", id, compiledId],
    queryFn: async () => unpack<FramesHeader>(await api.binary(`/api/shows/${id}/compiled/frames?fps=10`)),
    enabled: Boolean(compiledId),
  });
  const validation = useQuery({
    queryKey: keys.validation(id),
    queryFn: () => api.get<ValidationReport>(`/api/shows/${id}/validation`),
    enabled: Boolean(view.show.outputs.validation),
  });
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const clock = useRef({ time: 0, playing: false, speed: 1 });
  clock.current.playing = playing;
  clock.current.speed = speed;

  useEffect(() => {
    if (viewer.seek !== null) {
      clock.current.time = viewer.seek;
      setTime(viewer.seek);
      setPlaying(false);
      setViewer({ seek: null });
    }
  }, [viewer.seek, setViewer]);

  if (!compiledId) return <p className="p-6 text-muted-foreground">{t("viewer.noCompiled")}</p>;
  const duration = summary.data?.duration_s ?? 0;
  const fps = summary.data?.fps ?? 30;
  const violations = view.stale.validation === null ? validation.data?.violations ?? [] : [];

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="relative min-h-0 flex-1">
        <Stage>
          {frames.data && (
            <Drones
              frames={frames.data.arrays as Record<string, Float32Array | Uint8Array>}
              drones={frames.data.header.drones}
              clock={clock}
              duration={duration}
              onTime={setTime}
            />
          )}
        </Stage>
        <div className="absolute left-3 top-3 rounded bg-card/80 px-2 py-1 text-xs">
          {formatSeconds(time)} / {formatSeconds(duration)} · {summary.data?.drones} · {currentMark(summary.data, time * fps)}
        </div>
      </div>
      <div className="flex items-center gap-2 border-t px-2 py-1">
        <Button size="icon" variant="ghost" onClick={() => setPlaying(!playing)} title={playing ? t("viewer.pause") : t("viewer.play")}>
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </Button>
        <div className="relative flex-1">
          <input
            type="range"
            className="w-full accent-primary"
            min={0}
            max={duration}
            step={1 / fps}
            value={time}
            onChange={(e) => {
              clock.current.time = Number(e.target.value);
              setTime(clock.current.time);
            }}
          />
          <div className="pointer-events-none absolute inset-x-0 top-0 h-1">
            {violations.map((v, i) => (
              <button
                key={i}
                title={v.message}
                className="pointer-events-auto absolute top-0 h-2 min-w-[2px] -translate-y-1/2 bg-destructive"
                style={{ left: `${(v.t_start_s / duration) * 100}%`, width: `${Math.max(0.3, ((v.t_end_s - v.t_start_s) / duration) * 100)}%` }}
                onClick={() => {
                  clock.current.time = v.t_start_s;
                  setTime(v.t_start_s);
                }}
              />
            ))}
          </div>
        </div>
        <Select className="w-20" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} title={t("viewer.speed")}>
          {[0.25, 0.5, 1, 2, 4].map((s) => (
            <option key={s} value={s}>
              ×{s}
            </option>
          ))}
        </Select>
      </div>
    </div>
  );
}

function currentMark(summary: CompiledSummary | undefined, frame: number): string {
  const mark = summary?.marks.find((m) => m.start_frame <= frame && frame <= m.end_frame);
  return mark ? `${mark.kind}${mark.title ? `: ${mark.title}` : ""}` : "";
}

function Drones({
  frames,
  drones,
  clock,
  duration,
  onTime,
}: {
  frames: Record<string, Float32Array | Uint8Array>;
  drones: number;
  clock: React.MutableRefObject<{ time: number; playing: boolean; speed: number }>;
  duration: number;
  onTime: (t: number) => void;
}) {
  const texture = useMemo(dotTexture, []);
  const times = frames.times as Float32Array;
  const positions = frames.positions as Float32Array;
  const colors = frames.colors as Uint8Array;
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(drones * 3), 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(drones * 3), 3));
    return g;
  }, [drones]);
  const lastReport = useRef(0);

  useFrame((_, delta) => {
    const c = clock.current;
    if (c.playing) c.time = Math.min(duration, c.time + delta * c.speed);
    const position = geometry.getAttribute("position") as THREE.BufferAttribute;
    const color = geometry.getAttribute("color") as THREE.BufferAttribute;
    const index = interpolate(positions, times, drones, c.time, position.array as Float32Array);
    const base = index * drones * 3;
    const out = color.array as Float32Array;
    for (let i = 0; i < drones * 3; i++) out[i] = colors[base + i] / 255;
    position.needsUpdate = true;
    color.needsUpdate = true;
    if (Math.abs(c.time - lastReport.current) > 0.1 || !c.playing) {
      lastReport.current = c.time;
      onTime(c.time);
    }
  });

  return (
    <group rotation={SHOW_TO_THREE}>
      <points geometry={geometry} frustumCulled={false}>
        <pointsMaterial size={1.6} map={texture} vertexColors transparent depthWrite={false} blending={THREE.AdditiveBlending} sizeAttenuation />
      </points>
    </group>
  );
}
