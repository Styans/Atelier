// JSON shapes served by apps/api (mirrors of the Pydantic models in packages/domain).

export type Vec3 = [number, number, number];
export type Easing = "linear" | "ease_in_out" | "sine";
export type ViewerDirection = "+Y" | "-Y" | "+X" | "-X";
export type JsonSchema = Record<string, unknown>;

export interface FleetSpec {
  min_distance_m: number;
  max_speed_h_ms: number;
  max_speed_up_ms: number;
  max_speed_down_ms: number;
  max_accel_ms2: number;
  altitude_min_m: number;
  altitude_max_m: number;
  takeoff_grid_spacing_m: number;
  drone_count: number;
  preset: string | null;
}

export interface Brief {
  title: string;
  occasion: string | null;
  date: string | null;
  location: string | null;
  viewer_direction: ViewerDirection;
  duration_s: number;
  fleet: FleetSpec;
  mood: string | null;
  palette: string[];
  must_have: string[];
  forbidden: string[];
  notes: string | null;
  status: "draft" | "approved";
}

export interface StageConfig {
  component: string;
  params: Record<string, unknown>;
}

export interface ModelCard {
  id: string;
  name: string;
  kind: string;
  description: string;
  palette: string[];
  target_height_m: number | null;
  facing: "front" | "top" | "free";
  drone_count: number | null;
  color_source: "texture" | "palette" | "solid";
  status: string;
  image_prompt: string | null;
  assets: {
    concepts: string[];
    selected_concept: string | null;
    mesh: string | null;
    point_sets: Record<string, string>;
  };
  pipeline_overrides: Record<string, StageConfig>;
  version: number;
}

export type Region =
  | { type: "all" }
  | { type: "plane_split"; normal: Vec3; offset_m: number; side: "positive" | "negative" }
  | { type: "box"; min: Vec3; max: Vec3 };

export interface AnimTrack {
  primitive: string;
  region: Region;
  start_s: number;
  duration_s: number | null;
  easing: Easing;
  loop: boolean;
  params: Record<string, unknown>;
}

export interface Scene {
  id: string;
  title: string;
  model_id: string;
  hold_s: number;
  transition_in: { duration_s: number | null; planner: string | null; params: Record<string, unknown> };
  animation: { source_prompt: string; tracks: AnimTrack[]; color_tracks: unknown[] } | null;
  offset_m: Vec3;
  rotation_deg: Vec3;
}

export interface ArtifactRecord {
  id: string;
  kind: string;
  path: string;
  slot: string;
  producer: { component: string; plugin_id: string; plugin_version: string };
  created_at: string;
}

export interface Show {
  id: string;
  revision: number;
  seed: number;
  fps: number;
  brief: Brief;
  models: Record<string, ModelCard>;
  program: Scene[];
  pipeline: {
    point_distributor: StageConfig;
    transition_planner: StageConfig;
    validators: StageConfig[];
    exporters: StageConfig[];
    image_generator: StageConfig | null;
    mesh_generator: StageConfig | null;
  };
  flight: Record<string, unknown>;
  audio: {
    asset: { path: string; original_name: string };
    duration_s: number;
    sample_rate: number;
    offset_s: number | null;
  } | null;
  artifacts: Record<string, ArtifactRecord>;
  outputs: { compiled: string | null; validation: string | null; exports: Record<string, string> };
  stage_status: Record<string, string>;
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export type ChatBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error: boolean };

export interface ChatMessage {
  id: number;
  show_id: string;
  seq: number;
  role: "user" | "assistant" | "notice";
  content: ChatBlock[];
  turn_job_id: string | null;
  revision_before: number | null;
  revision_after: number | null;
  created_at: number;
  rolled_back_at: number | null;
}

export interface Proposal {
  id: string;
  show_id: string;
  action: string;
  args: Record<string, unknown>;
  summary: string;
  status: "pending" | "applied" | "dismissed";
  created_at: number;
}

export interface ChatState {
  messages: ChatMessage[];
  proposals: Proposal[];
  active_turn: Job | null;
}

export interface ChatEvent {
  id: number;
  turn_job_id: string;
  seq: number;
  kind: "text" | "tool_start" | "tool_result" | "status" | "done" | "error";
  payload: Record<string, unknown>;
}

export interface Job {
  id: string;
  show_id: string;
  kind: string;
  priority?: number;
  args: Record<string, unknown>;
  status: JobStatus;
  progress: number;
  message: string;
  error: string | null;
  result: unknown;
  created_at: number;
  finished_at: number | null;
  log_path: string | null;
}

export interface ShowView {
  show: Show;
  stale: Record<string, string | null>;
  jobs: Job[];
}

export interface ShowSummary {
  id: string;
  title: string;
  drones: number;
  duration_s: number;
  scenes: number;
  models: number;
  revision: number;
}

export interface Meta {
  version: string;
  presets: Record<string, Omit<FleetSpec, "drone_count" | "preset">>;
  roles: string[];
  job_kinds: string[];
  stages: string[];
}

export interface SceneMark {
  kind: "ground" | "takeoff" | "transition" | "hold" | "landing";
  scene_id: string | null;
  title: string;
  start_frame: number;
  end_frame: number;
}

export interface TimelineSegment {
  kind: "takeoff" | "transition" | "hold" | "landing";
  scene_id: string | null;
  title: string;
  start_s: number;
  duration_s: number;
  basis: "exact" | "points" | "proxy" | "fixed";
}

/** GET /api/shows/{id}/timeline: exact from the compiled show, otherwise estimated (ADR-038). */
export interface TimelineEstimate {
  segments: TimelineSegment[];
  total_s: number;
  budget_s: number;
  over_budget_s: number;
  compiled: boolean;
  music_start_s: number | null;
  music_end_s: number | null;
  notes: string[];
}

export interface CompiledSummary {
  frames: number;
  drones: number;
  fps: number;
  duration_s: number;
  marks: SceneMark[];
  audio: { offset_s: number; duration_s: number } | null;
  meta: { warnings?: string[]; budget_s?: number; music_start_s?: number | null };
}

export interface Violation {
  rule: string;
  severity: "error" | "warning";
  frame_start: number;
  frame_end: number;
  t_start_s: number;
  t_end_s: number;
  drones: number[];
  value: number;
  limit: number;
  unit: string;
  message: string;
  scene_id: string | null;
  segment: string | null;
}

export interface ValidationReport {
  ok: boolean;
  metrics: Record<string, number>;
  rules: { component: string; violations: number; truncated: number }[];
  violations: Violation[];
}

export interface ComponentInfo {
  id: string;
  role?: string;
  plugin_id?: string;
  name: string;
  version: string;
  description: string;
  params_schema: JsonSchema;
}

export interface PluginInfo {
  id: string;
  name: string;
  version: string;
  runtime: "inprocess" | "subprocess";
  roles: string[];
  requires_gpu: boolean;
  description: string;
  settings: Record<string, string>;
  status: "ok" | "degraded" | "error" | "disabled";
  error: string | null;
  health: { status: string; message: string } | null;
  components: ComponentInfo[];
}

export interface RevisionInfo {
  revision: number;
  message: string;
  author: string;
  created_at: string;
}

export interface DiffEntry {
  path: string;
  old: string | null;
  new: string | null;
}

export interface ExportInfo {
  component: string;
  folder: string;
  files: string[];
  created_at: string;
}
