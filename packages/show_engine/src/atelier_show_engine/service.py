"""The show service: every platform action, for the CLI now and the API later.

Each mutating method loads the current revision, changes it and commits a new revision.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from pydantic import ValidationError
from scipy.spatial import cKDTree

from atelier_domain import (
    AnimationSpec,
    AnimTrack,
    ArtifactRecord,
    AudioTrack,
    Brief,
    FlightSettings,
    ModelCard,
    Scene,
    Show,
    StageConfig,
    TransitionSpec,
    Vec3,
    ViewerDirection,
    derive_seed,
    fingerprint,
    fleet_from_preset,
)
from atelier_domain.ids import new_show_id, unique_id
from atelier_plugin_host import PluginRegistry
from atelier_plugin_sdk import (
    CompiledShow,
    CompiledShowRef,
    ImageRef,
    MeshRef,
    PluginError,
    PointSet,
    RuleResult,
    Violation,
    linear_to_srgb8,
)
from atelier_plugin_sdk.roles import ROLE_NAMES, RoleName
from atelier_show_engine.compiler import CompileError, Compiler, CompileReport, figure_colors
from atelier_show_engine.config import AtelierConfig
from atelier_show_engine.diffing import DiffEntry, diff_shows
from atelier_show_engine.estimate import (
    PointsLoader,
    Segment,
    TimelineEstimate,
    estimate_timeline,
)
from atelier_show_engine.geometry import place
from atelier_show_engine.importing import UpAxis, copy_asset, load_mesh_file, probe_audio
from atelier_show_engine.program import FitResult, SceneDraft, fit_holds
from atelier_show_engine.provenance import (
    IMPORT_MESH,
    Expectation,
    Provenance,
    Unavailable,
    export_slot,
    mesh_slot,
    new_record,
    points_slot,
    producer_for,
)
from atelier_show_engine.store import ConflictError, ShowStore, StoreError
from atelier_show_engine.summary import show_summary

Progress = Callable[[float, str], None]
SINGLE_STAGE_ROLES: tuple[RoleName, ...] = (
    "point_distributor",
    "transition_planner",
    "image_generator",
    "mesh_generator",
)


BRIEF_FIELDS = frozenset(
    {
        "title",
        "occasion",
        "date",
        "location",
        "viewer_direction",
        "duration_s",
        "mood",
        "palette",
        "must_have",
        "forbidden",
        "notes",
        "status",
    }
)
MODEL_FIELDS = frozenset(
    {
        "name",
        "kind",
        "description",
        "style",
        "image_prompt",
        "negative_prompt",
        "palette",
        "target_height_m",
        "facing",
        "drone_count",
        "color_source",
        "status",
        "pipeline_overrides",
    }
)
SCENE_FIELDS = frozenset(
    {"title", "model_id", "hold_s", "transition_in", "animation", "offset_m", "rotation_deg"}
)


class ServiceError(Exception):
    pass


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors()
    )


def _scene_index(show: Show, scene_id: str) -> int:
    for index, scene in enumerate(show.program):
        if scene.id == scene_id:
            return index
    raise ServiceError(f"no scene {scene_id!r}")


@dataclass
class PointsPreview:
    positions: npt.NDArray[np.float32]  # placed in the show, metres
    colors: npt.NDArray[np.uint8]  # sRGB LED colours
    nearest_m: npt.NDArray[np.float32]  # distance to the nearest drone
    scale: float
    min_distance_m: float
    stale: str | None
    metrics: dict[str, float]


@dataclass
class ValidationSummary:
    ok: bool
    errors: int
    warnings: int
    metrics: dict[str, float]
    violations: list[Violation]
    path: Path


@dataclass
class ExportSummary:
    component: str
    folder: Path
    files: list[Path]


def _dir_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        digest.update(path.relative_to(folder).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _points_loader(show: Show, store: ShowStore) -> PointsLoader:
    """Figure positions for the estimate; stale points are still a good guess of the shape."""

    def load(model_id: str, n: int) -> npt.NDArray[np.float64] | None:
        card = show.models.get(model_id)
        artifact_id = card.assets.point_sets.get(n) if card else None
        if artifact_id is None:
            return None
        positions: npt.NDArray[np.float64] = PointSet.load(
            store.path(show.artifacts[artifact_id].path)
        ).positions.astype(np.float64)
        return positions

    return load


def _compiled_timeline(show: Show, store: ShowStore) -> TimelineEstimate:
    compiled = CompiledShow.load(store.path(show.artifacts[show.outputs.compiled or ""].path))
    fps = compiled.fps
    segments = [
        Segment(
            kind=m.kind,
            scene_id=m.scene_id,
            title=m.title,
            start_s=(m.start_frame - 1) / fps,
            duration_s=(m.end_frame - m.start_frame + 1) / fps,
            basis="exact",
        )
        for m in compiled.marks
        if m.kind != "ground"
    ]
    timeline = TimelineEstimate(segments, show.brief.duration_s, compiled=True)
    if compiled.audio is not None:
        timeline.music_start_s = compiled.audio.offset_s
        timeline.music_end_s = compiled.audio.offset_s + compiled.audio.duration_s
    return timeline


class ShowService:
    def __init__(
        self, config: AtelierConfig, registry: PluginRegistry | None = None, author: str = "cli"
    ) -> None:
        self.config = config
        self._registry = registry
        self.author = author  # recorded in the revision history: cli, ui, worker, assistant

    def _commit(self, store: ShowStore, show: Show, message: str) -> Show:
        return store.commit(show, message, self.author)

    @property
    def registry(self) -> PluginRegistry:
        if self._registry is None:
            self._registry = PluginRegistry.discover(
                self.config.plugin_dirs,
                config=self.config.host_config(),
                disabled=self.config.disabled_plugins,
                cache_path=self.config.cache_dir / "plugins.json",
                log_dir=self.config.cache_dir / "logs",
                plugin_settings=self.config.plugin_settings,
            )
        return self._registry

    def close(self) -> None:
        if self._registry is not None:
            self._registry.close()

    def __enter__(self) -> ShowService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- projects ---

    def create_show(
        self,
        title: str,
        *,
        drones: int,
        duration_s: float,
        preset: str = "conservative",
        viewer: ViewerDirection = "-Y",
        seed: int = 0,
    ) -> tuple[ShowStore, Show]:
        try:
            fleet = fleet_from_preset(preset, drones, self.config.presets)
        except KeyError as exc:
            raise ServiceError(str(exc.args[0])) from exc
        show = Show(
            id=new_show_id(title),
            seed=seed,
            brief=Brief(title=title, duration_s=duration_s, fleet=fleet, viewer_direction=viewer),
            pipeline=self.config.pipeline.model_copy(deep=True),
        )
        return ShowStore.create(self.config.projects_dir, show)

    def open(self, ref: str | Path) -> ShowStore:
        """A show by id (inside the projects folder) or by path."""
        candidate = Path(ref)
        try:
            if candidate.exists():
                return ShowStore.open(candidate)
            return ShowStore.open(self.config.projects_dir / str(ref))
        except StoreError as exc:
            raise ServiceError(str(exc)) from exc

    def list_shows(self) -> list[Show]:
        shows = []
        for path in sorted(self.config.projects_dir.glob("*/show.json")):
            with contextlib.suppress(Exception):
                shows.append(ShowStore(path.parent).load())
        return shows

    @contextlib.contextmanager
    def _work(self, store: ShowStore) -> Iterator[Path]:
        """Scratch folder for one operation; removed when the operation succeeds."""
        workdir = store.workdir()
        yield workdir
        shutil.rmtree(workdir, ignore_errors=True)

    # --- brief, fleet, music ---

    def use_fleet_preset(self, store: ShowStore, name: str) -> Show:
        show = store.load()
        try:
            show.brief.fleet = fleet_from_preset(
                name, show.brief.fleet.drone_count, self.config.presets
            )
        except KeyError as exc:
            raise ServiceError(str(exc.args[0])) from exc
        return self._commit(store, show, f"Fleet preset {name}")

    def set_fleet(self, store: ShowStore, **values: Any) -> Show:
        show = store.load()
        data = show.brief.fleet.model_dump() | {k: v for k, v in values.items() if v is not None}
        data["preset"] = None if values.keys() - {"drone_count"} else data["preset"]
        show.brief.fleet = type(show.brief.fleet).model_validate(data)
        return self._commit(
            store,
            show,
            "Fleet: " + ", ".join(f"{k}={v}" for k, v in values.items() if v is not None),
        )

    def set_music(self, store: ShowStore, path: Path, offset_s: float | None = None) -> Show:
        duration, rate = probe_audio(path)
        show = store.load()
        show.audio = AudioTrack(
            asset=copy_asset(store.root, path),
            duration_s=duration,
            sample_rate=rate,
            offset_s=offset_s,
        )
        return self._commit(store, show, f"Music {path.name} ({duration:.1f} s)")

    # --- models and points ---

    def add_model(
        self,
        store: ShowStore,
        source: Path,
        *,
        name: str | None = None,
        target_height_m: float | None = None,
        up: UpAxis = "y",
        color_source: str = "texture",
        palette: Sequence[str] = (),
    ) -> tuple[Show, str]:
        mesh = load_mesh_file(source, up)
        show = store.load()
        name = name or source.stem
        model_id = unique_id("m", name, show.models.keys())
        asset = copy_asset(store.root, source)
        expectation = Expectation(IMPORT_MESH, {"source": asset.content_hash, "up": up}, None, [])
        fp = fingerprint(expectation.producer, expectation.params, None, [])
        target = store.new_file(f"models/{model_id}", "mesh", fp, ".npz")
        mesh.save(target)
        record = new_record(
            kind="mesh",
            slot=mesh_slot(model_id),
            path=store.relative(target),
            expectation=expectation,
            input_records=[],
            content_hash=mesh.content_hash(),
        )
        card = ModelCard.model_validate(
            {
                "id": model_id,
                "name": name,
                "target_height_m": target_height_m,
                "color_source": color_source,
                "palette": list(palette),
                "status": "ready",
            }
        )
        card.assets.mesh = record.id
        show.models[model_id] = card
        show.artifacts[record.id] = record
        return self._commit(store, show, f"Add model {name} from {source.name}"), model_id

    def create_model(self, store: ShowStore, name: str, **fields: Any) -> tuple[Show, str]:
        """An empty model card, to be filled by generation (concepts, then a 3D model)."""
        unknown = set(fields) - MODEL_FIELDS
        if unknown:
            raise ServiceError(f"unknown model fields: {sorted(unknown)}")
        show = store.load()
        model_id = unique_id("m", name, show.models.keys())
        try:
            card = ModelCard.model_validate({"id": model_id, "name": name, **fields})
        except ValidationError as exc:
            raise ServiceError(_validation_message(exc)) from exc
        show.models[model_id] = card
        return self._commit(store, show, f"New model card {name}"), model_id

    def compute_points(
        self,
        store: ShowStore,
        model_id: str,
        n: int | None = None,
        on_progress: Progress | None = None,
    ) -> ArtifactRecord:
        show = store.load()
        if model_id not in show.models:
            raise ServiceError(f"no model {model_id!r}; models: {', '.join(show.models) or 'none'}")
        if show.models[model_id].assets.mesh is None:
            raise ServiceError(
                f"model {model_id!r} has no 3D model yet: import a file or generate one"
            )
        prov = Provenance(show, self.registry)
        n = n or prov.figure_drone_count(model_id)
        slot = points_slot(model_id, n)
        try:
            expectation = prov.expectation(slot)
            mesh = prov.require_fresh([mesh_slot(model_id)])[0]
            handle, params = prov.stage("point_distributor", prov.point_stage(model_id))
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc
        fp = fingerprint(
            expectation.producer, expectation.params, expectation.seed, [mesh.fingerprint]
        )
        with self._work(store) as workdir:
            try:
                ref = handle.invoke(
                    {"source": MeshRef(path=store.path(mesh.path)), "n": n},
                    params,
                    seed=expectation.seed or 0,
                    workdir=workdir,
                    on_progress=on_progress,
                )
            except PluginError as exc:
                raise ServiceError(f"{handle.info.id}: {exc.message}") from exc
            target = store.new_file(f"models/{model_id}", f"points_{n}", fp, ".npz")
            shutil.move(ref.path, target)
        record = new_record(
            kind="point_set",
            slot=slot,
            path=store.relative(target),
            expectation=expectation,
            input_records=[mesh],
            content_hash=PointSet.load(target).content_hash(),
        )
        show.artifacts[record.id] = record
        show.models[model_id].assets.point_sets[n] = record.id
        self._commit(store, show, f"Points for {model_id}: {n} by {handle.info.id}")
        return record

    # --- generation (roles image_generator / mesh_generator) ---

    def _generator_stage(self, show: Show, model_id: str, role: RoleName) -> StageConfig:
        card = show.models[model_id]
        config: StageConfig | None = card.pipeline_overrides.get(role) or getattr(
            show.pipeline, role
        )
        if config is None:
            raise ServiceError(f"no {role} in the pipeline; choose one first (pipeline set {role})")
        return config

    def generate_concepts(
        self,
        store: ShowStore,
        model_id: str,
        prompt: str,
        n: int = 4,
        on_progress: Progress | None = None,
    ) -> ArtifactRecord:
        """Concept images for a model card; every set is kept, never overwritten."""
        show = store.load()
        if model_id not in show.models:
            raise ServiceError(f"no model {model_id!r}")
        card = show.models[model_id]
        prov = Provenance(show, self.registry)
        try:
            config = self._generator_stage(show, model_id, "image_generator")
            handle, params = prov.stage("image_generator", config)
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc
        slot = f"models.{model_id}.concepts"
        expectation = Expectation(
            producer=producer_for(handle),
            params={
                "component": params,
                "prompt": prompt,
                "negative": card.negative_prompt,
                "n": n,
            },
            seed=derive_seed(show.seed, f"{slot}.{len(card.assets.concepts)}"),
            inputs=[],
        )
        fp = fingerprint(expectation.producer, expectation.params, expectation.seed, [])
        with self._work(store) as workdir:
            try:
                ref = handle.invoke(
                    {"prompt": prompt, "negative": card.negative_prompt, "n": n},
                    params,
                    seed=expectation.seed or 0,
                    workdir=workdir,
                    on_progress=on_progress,
                )
            except PluginError as exc:
                raise ServiceError(f"{handle.info.id}: {exc.message}") from exc
            folder = store.new_file(f"models/{model_id}", "concepts", fp, "")
            folder.mkdir(parents=True)
            names = list(ref.meta.get("files") or sorted(p.name for p in ref.path.iterdir()))
            for name in names:
                shutil.copyfile(ref.path / name, folder / name)
        record = new_record(
            kind="image_set",
            slot=slot,
            path=store.relative(folder),
            expectation=expectation,
            input_records=[],
            content_hash=_dir_hash(folder),
        )
        show.artifacts[record.id] = record
        card.assets.concepts.append(record.id)
        card.image_prompt = prompt
        self._commit(store, show, f"Concepts for {model_id}: {len(names)} by {handle.info.id}")
        return record

    def concept_images(self, store: ShowStore, model_id: str) -> list[str]:
        """Every concept image of a model as ``<artifact id>/<file name>``, newest set first."""
        show = store.load()
        if model_id not in show.models:
            raise ServiceError(f"no model {model_id!r}")
        result = []
        for artifact_id in reversed(show.models[model_id].assets.concepts):
            folder = store.path(show.artifacts[artifact_id].path)
            result += [f"{artifact_id}/{p.name}" for p in sorted(folder.iterdir()) if p.is_file()]
        return result

    def generate_mesh(
        self,
        store: ShowStore,
        model_id: str,
        concept: str | None = None,
        on_progress: Progress | None = None,
    ) -> ArtifactRecord:
        """A 3D model from a concept image; it becomes the model's current mesh (ADR-027)."""
        show = store.load()
        if model_id not in show.models:
            raise ServiceError(f"no model {model_id!r}")
        card = show.models[model_id]
        concept = (
            concept
            or card.assets.selected_concept
            or next(iter(self.concept_images(store, model_id)), None)
        )
        if concept is None:
            raise ServiceError(f"model {model_id!r} has no concept images yet")
        artifact_id, _, file_name = concept.partition("/")
        concept_record = show.artifacts.get(artifact_id)
        if concept_record is None or not (store.path(concept_record.path) / file_name).is_file():
            raise ServiceError(f"no concept image {concept!r}")
        prov = Provenance(show, self.registry)
        try:
            config = self._generator_stage(show, model_id, "mesh_generator")
            handle, params = prov.stage("mesh_generator", config)
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc
        expectation = Expectation(
            producer=producer_for(handle),
            params={"component": params, "concept": concept},
            seed=derive_seed(show.seed, f"models.{model_id}.mesh.{concept}"),
            inputs=[],
        )
        fp = fingerprint(
            expectation.producer, expectation.params, expectation.seed, [concept_record.fingerprint]
        )
        with self._work(store) as workdir:
            try:
                ref = handle.invoke(
                    {"source": ImageRef(path=store.path(concept_record.path) / file_name)},
                    params,
                    seed=expectation.seed or 0,
                    workdir=workdir,
                    on_progress=on_progress,
                )
            except PluginError as exc:
                raise ServiceError(f"{handle.info.id}: {exc.message}") from exc
            if ref.is_mesh_data:
                mesh = ref.load()
            else:
                asset = copy_asset(store.root, ref.path)
                mesh = load_mesh_file(store.path(asset.path), "y")
        target = store.new_file(f"models/{model_id}", "mesh", fp, ".npz")
        mesh.save(target)
        record = new_record(
            kind="mesh",
            slot=mesh_slot(model_id),
            path=store.relative(target),
            expectation=expectation,
            input_records=[concept_record],
            content_hash=mesh.content_hash(),
        )
        show.artifacts[record.id] = record
        card.assets.mesh = record.id
        card.assets.selected_concept = concept
        card.status = "ready"
        self._commit(store, show, f"Mesh for {model_id} from {concept} by {handle.info.id}")
        return record

    # --- program ---

    def add_scene(
        self,
        store: ShowStore,
        model_id: str,
        *,
        hold_s: float,
        title: str | None = None,
        transition_s: float | None = None,
        planner: str | None = None,
        planner_params: dict[str, Any] | None = None,
        tracks: Sequence[AnimTrack] = (),
        offset_m: Vec3 = (0.0, 0.0, 0.0),
        rotation_deg: Vec3 = (0.0, 0.0, 0.0),
    ) -> Scene:
        show = store.load()
        if model_id not in show.models:
            raise ServiceError(f"no model {model_id!r}")
        prov = Provenance(show, self.registry)
        try:
            for track in tracks:
                prov.stage(
                    "animation_primitive",
                    StageConfig(component=track.primitive, params=track.params),
                )
            if planner:
                prov.stage(
                    "transition_planner",
                    StageConfig(component=planner, params=planner_params or {}),
                )
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc
        scene = Scene(
            id=unique_id("s", str(len(show.program) + 1), {s.id for s in show.program}),
            title=title or show.models[model_id].name,
            model_id=model_id,
            hold_s=hold_s,
            transition_in=TransitionSpec(
                duration_s=transition_s, planner=planner, params=planner_params or {}
            ),
            animation=AnimationSpec(tracks=list(tracks)) if tracks else None,
            offset_m=offset_m,
            rotation_deg=rotation_deg,
        )
        show.program.append(scene)
        self._commit(store, show, f"Add scene {scene.title}")
        return scene

    # --- plugins ---

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Enable or disable a plugin now and remember the choice in .atelier/ui_settings.json."""
        self.registry.set_enabled(plugin_id, enabled)
        disabled = set(self.config.ui_settings().get("disabled_plugins", []))
        disabled = disabled - {plugin_id} if enabled else disabled | {plugin_id}
        self.config.save_ui_settings({"disabled_plugins": sorted(disabled)})

    # --- edits (UI and API) ---

    def check_revision(self, store: ShowStore, base_revision: int | None) -> None:
        """Refuse an edit made on an older revision (the UI reloads and shows the change)."""
        if base_revision is not None:
            current = store.current_revision()
            if current != base_revision:
                raise ConflictError(
                    f"the show changed meanwhile (now revision {current}, "
                    f"the edit was based on {base_revision})"
                )

    def _edit(self, store: ShowStore, message: str, change: Callable[[Show], None]) -> Show:
        show = store.load()
        try:
            change(show)
            show = Show.model_validate(show.model_dump(mode="json"))
        except ValidationError as exc:
            raise ServiceError(_validation_message(exc)) from exc
        return self._commit(store, show, message)

    def update_brief(self, store: ShowStore, changes: dict[str, Any]) -> Show:
        unknown = set(changes) - BRIEF_FIELDS
        if unknown:
            raise ServiceError(f"brief fields that cannot be edited here: {sorted(unknown)}")

        def change(show: Show) -> None:
            show.brief = Brief.model_validate(show.brief.model_dump(mode="json") | changes)

        return self._edit(store, "Brief: " + ", ".join(sorted(changes)), change)

    def update_flight(self, store: ShowStore, changes: dict[str, Any]) -> Show:
        def change(show: Show) -> None:
            show.flight = FlightSettings.model_validate(
                show.flight.model_dump(mode="json") | changes
            )

        return self._edit(store, "Flight: " + ", ".join(sorted(changes)), change)

    def update_music(self, store: ShowStore, offset_s: float | None) -> Show:
        def change(show: Show) -> None:
            if show.audio is None:
                raise ServiceError("the show has no music")
            show.audio.offset_s = offset_s

        return self._edit(store, f"Music offset {offset_s}", change)

    def update_model(self, store: ShowStore, model_id: str, changes: dict[str, Any]) -> Show:
        unknown = set(changes) - MODEL_FIELDS
        if unknown:
            raise ServiceError(f"model fields that cannot be edited here: {sorted(unknown)}")
        show = store.load()
        if model_id not in show.models:
            raise ServiceError(f"no model {model_id!r}")
        overrides = changes.get("pipeline_overrides") or {}
        prov = Provenance(show, self.registry)
        try:
            for role, config in overrides.items():
                if role not in ROLE_NAMES:
                    raise ServiceError(f"unknown role {role!r}")
                prov.stage(role, StageConfig.model_validate(config))
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc

        def change(show: Show) -> None:
            card = show.models[model_id]
            show.models[model_id] = ModelCard.model_validate(
                card.model_dump(mode="json") | changes | {"version": card.version + 1}
            )

        return self._edit(store, f"Model {model_id}: " + ", ".join(sorted(changes)), change)

    def remove_model(self, store: ShowStore, model_id: str) -> Show:
        """Remove a model card; its files stay in the project (nothing is deleted)."""

        def change(show: Show) -> None:
            if model_id not in show.models:
                raise ServiceError(f"no model {model_id!r}")
            users = [s.id for s in show.program if s.model_id == model_id]
            if users:
                raise ServiceError(f"model {model_id!r} is used by scenes {users}")
            del show.models[model_id]

        return self._edit(store, f"Remove model {model_id}", change)

    def update_scene(self, store: ShowStore, scene_id: str, changes: dict[str, Any]) -> Show:
        unknown = set(changes) - SCENE_FIELDS
        if unknown:
            raise ServiceError(f"scene fields that cannot be edited here: {sorted(unknown)}")
        show = store.load()
        index = _scene_index(show, scene_id)
        try:
            scene = Scene.model_validate(show.program[index].model_dump(mode="json") | changes)
        except ValidationError as exc:
            raise ServiceError(_validation_message(exc)) from exc
        self._check_components(show, scene)

        def change(show: Show) -> None:
            show.program[index] = scene

        return self._edit(store, f"Scene {scene_id}: " + ", ".join(sorted(changes)), change)

    def _check_components(self, show: Show, scene: Scene) -> None:
        prov = Provenance(show, self.registry)
        try:
            for track in scene.animation.tracks if scene.animation else []:
                prov.stage(
                    "animation_primitive",
                    StageConfig(component=track.primitive, params=track.params),
                )
            if scene.transition_in.planner:
                prov.stage(
                    "transition_planner",
                    StageConfig(
                        component=scene.transition_in.planner, params=scene.transition_in.params
                    ),
                )
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc

    def remove_scene(self, store: ShowStore, scene_id: str) -> Show:
        def change(show: Show) -> None:
            del show.program[_scene_index(show, scene_id)]

        return self._edit(store, f"Remove scene {scene_id}", change)

    def reorder_scenes(self, store: ShowStore, order: Sequence[str]) -> Show:
        def change(show: Show) -> None:
            if sorted(order) != sorted(s.id for s in show.program):
                raise ServiceError("the new order must list every scene exactly once")
            by_id = {s.id: s for s in show.program}
            show.program = [by_id[i] for i in order]

        return self._edit(store, "Reorder scenes: " + ", ".join(order), change)

    def approve_stage(self, store: ShowStore, stage: str, status: str = "approved") -> Show:
        def change(show: Show) -> None:
            show.stage_status = {**show.stage_status, stage: status}  # type: ignore[dict-item]

        return self._edit(store, f"Stage {stage}: {status}", change)

    def revision_diff(self, store: ShowStore, old: int, new: int | None = None) -> list[DiffEntry]:
        """What changed between two revisions, path by path (artifacts summarized)."""
        try:
            before = store.load_revision(old)
            after = store.load() if new is None else store.load_revision(new)
        except StoreError as exc:
            raise ServiceError(str(exc)) from exc
        return diff_shows(before, after)

    def points_preview(
        self, store: ShowStore, model_id: str, n: int | None = None
    ) -> PointsPreview:
        """A point set ready for the viewer: positions at the safe scale, colours, spacing."""
        show = store.load()
        if model_id not in show.models:
            raise ServiceError(f"no model {model_id!r}")
        prov = Provenance(show, self.registry)
        n = n or prov.figure_drone_count(model_id)
        record = prov.record(points_slot(model_id, n))
        if record is None:
            raise ServiceError(f"no points for {model_id} at {n} drones yet")
        points = PointSet.load(store.path(record.path))
        card = show.models[model_id]
        scene = next((s for s in show.program if s.model_id == model_id), None) or Scene(
            id="preview", title="preview", model_id=model_id, hold_s=1
        )
        placement = place(
            points.positions,
            card,
            scene,
            show.brief.fleet,
            show.flight,
            show.brief.viewer_direction,
        )
        world = placement.to_world(placement.local)
        nearest = (
            cKDTree(world).query(world, k=2)[0][:, 1] if len(world) > 1 else np.zeros(len(world))
        )
        return PointsPreview(
            positions=world.astype(np.float32),
            colors=linear_to_srgb8(figure_colors(card, points)),
            nearest_m=nearest.astype(np.float32),
            scale=placement.scale,
            min_distance_m=show.brief.fleet.min_distance_m,
            stale=prov.stale_reason(record.slot),
            metrics=dict(points.meta.get("metrics", {})),
        )

    # --- pipeline ---

    def set_stage(
        self, store: ShowStore, role: RoleName, component: str, params: dict[str, Any]
    ) -> Show:
        if role not in SINGLE_STAGE_ROLES:
            raise ServiceError(f"role {role!r} is not a single pipeline stage")
        show = store.load()
        config = StageConfig(component=component, params=params)
        try:
            Provenance(show, self.registry).stage(role, config)
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc
        setattr(show.pipeline, role, config)
        return self._commit(store, show, f"Pipeline {role}: {component} {json.dumps(params)}")

    def set_stage_list(self, store: ShowStore, which: str, components: Sequence[str]) -> Show:
        role: RoleName = {"validators": "validator_rule", "exporters": "exporter"}[which]  # type: ignore[assignment]
        show = store.load()
        configs = [StageConfig(component=c) for c in components]
        prov = Provenance(show, self.registry)
        try:
            for config in configs:
                prov.stage(role, config)
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc
        setattr(show.pipeline, which, configs)
        return self._commit(store, show, f"Pipeline {which}: {', '.join(components)}")

    # --- status ---

    def stale_report(self, store: ShowStore) -> dict[str, str | None]:
        return Provenance(store.load(), self.registry).report()

    def estimate_timeline(self, store: ShowStore) -> TimelineEstimate:
        """The program's timeline: exact from the current compiled show, else estimated."""
        show = store.load()
        if (
            show.outputs.compiled
            and Provenance(show, self.registry).stale_reason("compiled") is None
        ):
            return _compiled_timeline(show, store)
        return estimate_timeline(show, _points_loader(show, store))

    # --- programs (ADR-037) ---

    def apply_program(
        self,
        store: ShowStore,
        drafts: Sequence[SceneDraft],
        *,
        replace: bool = False,
        message: str | None = None,
    ) -> tuple[Show, FitResult]:
        """A whole program in one revision: new cards, scenes, holds fitted to the budget."""
        if not drafts:
            raise ServiceError("a program needs at least one scene")
        show = store.load()
        if show.program and not replace:
            raise ServiceError(
                f"the show already has {len(show.program)} scenes; replacing them removes them"
            )
        scenes: list[Scene] = []
        # A figure shown twice (the logo at the start and at the end) is one card.
        made: dict[str, str] = {}
        for index, draft in enumerate(drafts, start=1):
            if draft.model_id is not None:
                if draft.model_id not in show.models:
                    raise ServiceError(f"scene {index}: no model {draft.model_id!r}")
                model_id = draft.model_id
            elif draft.figure is not None:
                key = " ".join(draft.figure.name.casefold().split())
                if key not in made:
                    made[key] = unique_id("m", draft.figure.name, show.models.keys())
                    show.models[made[key]] = ModelCard(
                        id=made[key], **draft.figure.model_dump(exclude_none=True)
                    )
                model_id = made[key]
            else:
                raise ServiceError(f"scene {index}: give a new figure or an existing model_id")
            scenes.append(
                Scene(id=f"s_{index}", title=draft.title, model_id=model_id, hold_s=draft.hold_s)
            )
        show.program = scenes
        fit = fit_holds(
            estimate_timeline(show, _points_loader(show, store)),
            {s.id: s.hold_s for s in scenes},
        )
        for scene in show.program:
            scene.hold_s = fit.holds.get(scene.id, scene.hold_s)
        try:
            show = Show.model_validate(show.model_dump(mode="json"))
        except ValidationError as exc:
            raise ServiceError(_validation_message(exc)) from exc
        return self._commit(store, show, message or f"Program: {len(scenes)} scenes"), fit

    def fit_program(self, store: ShowStore) -> tuple[Show, FitResult]:
        """Fit the holds of the current program to the brief's duration."""
        show = store.load()
        if not show.program:
            raise ServiceError("the program has no scenes")
        fit = fit_holds(
            estimate_timeline(show, _points_loader(show, store)),
            {s.id: s.hold_s for s in show.program},
        )

        def change(target: Show) -> None:
            for scene in target.program:
                scene.hold_s = fit.holds.get(scene.id, scene.hold_s)

        total = f"{fit.total_s:.0f} of {fit.budget_s:.0f} s"
        return self._edit(store, f"Fit holds to the budget ({total})", change), fit

    def show_summary(self, store: ShowStore) -> str:
        """The compact picture of the show the assistant reads every turn (ADR-040)."""
        show = store.load()
        stale = Provenance(show, self.registry).report()
        validation = None
        if show.outputs.validation and stale.get("validation") is None:
            report = json.loads(
                store.path(show.artifacts[show.outputs.validation].path).read_text(encoding="utf-8")
            )
            validation = (bool(report.get("ok")), len(report.get("violations", [])))
        return show_summary(show, self.estimate_timeline(store), stale, validation)

    # --- compile, validate, export ---

    def compile(
        self, store: ShowStore, *, allow_stale: bool = False, on_progress: Progress | None = None
    ) -> tuple[ArtifactRecord, CompileReport]:
        show = store.load()
        prov = Provenance(show, self.registry)
        with self._work(store) as workdir:
            try:
                compiled, report, expectation, inputs = Compiler(
                    store, show, prov, workdir
                ).compile(allow_stale=allow_stale, on_progress=on_progress)
            except PluginError as exc:
                raise CompileError(exc.message) from exc
        fp = fingerprint(
            expectation.producer,
            expectation.params,
            expectation.seed,
            [r.fingerprint for r in inputs],
        )
        target = store.new_file("compiled", "trajectories", fp, ".npz")
        compiled.save(target)
        record = new_record(
            kind="compiled_show",
            slot="compiled",
            path=store.relative(target),
            expectation=expectation,
            input_records=inputs,
            content_hash=compiled.content_hash(),
        )
        show.artifacts[record.id] = record
        show.outputs.compiled = record.id
        self._commit(store, show, f"Compile: {report.frames} frames, {report.duration_s:.1f} s")
        return record, report

    def _fresh_compiled(
        self, prov: Provenance, store: ShowStore, allow_stale: bool
    ) -> ArtifactRecord:
        record = prov.record("compiled")
        if record is None:
            raise ServiceError("the show is not compiled yet; run `atelier compile`")
        reason = prov.stale_reason("compiled")
        if reason is not None and not allow_stale:
            raise ServiceError(f"the compiled show is stale ({reason}); run `atelier compile`")
        return record

    def validate(self, store: ShowStore, *, allow_stale: bool = False) -> ValidationSummary:
        show = store.load()
        prov = Provenance(show, self.registry)
        compiled_record = self._fresh_compiled(prov, store, allow_stale)
        try:
            expectation = prov.expectation("validation")
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc
        ref = CompiledShowRef(path=store.path(compiled_record.path))
        compiled = ref.load()
        rules: list[dict[str, Any]] = []
        violations: list[Violation] = []
        metrics: dict[str, float] = {}
        with self._work(store) as workdir:
            for config in show.pipeline.validators:
                handle, params = prov.stage("validator_rule", config)
                try:
                    result: RuleResult = handle.invoke(
                        {"show": ref, "fleet": show.brief.fleet}, params, seed=0, workdir=workdir
                    )
                except PluginError as exc:
                    raise ServiceError(f"{handle.info.id}: {exc.message}") from exc
                for violation in result.violations:
                    mark = compiled.mark_at(violation.frame_start)
                    violation.scene_id = mark.scene_id if mark else None
                    violation.segment = mark.kind if mark else None
                violations += result.violations
                metrics |= result.metrics
                rules.append(
                    {
                        "component": handle.info.id,
                        "violations": len(result.violations),
                        "truncated": result.truncated,
                        "metrics": result.metrics,
                    }
                )
        errors = sum(v.severity == "error" for v in violations)
        report = {
            "show_id": show.id,
            "compiled": compiled_record.id,
            "fleet": show.brief.fleet.model_dump(mode="json"),
            "ok": errors == 0,
            "rules": rules,
            "metrics": metrics,
            "violations": [v.model_dump(mode="json") for v in violations],
        }
        fp = fingerprint(
            expectation.producer, expectation.params, None, [compiled_record.fingerprint]
        )
        target = store.new_file("compiled", "validation", fp, ".json")
        target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        record = new_record(
            kind="validation_report",
            slot="validation",
            path=store.relative(target),
            expectation=expectation,
            input_records=[compiled_record],
            content_hash=hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest(),
        )
        show.artifacts[record.id] = record
        show.outputs.validation = record.id
        self._commit(store, show, f"Validate: {errors} errors")
        return ValidationSummary(
            ok=errors == 0,
            errors=errors,
            warnings=len(violations) - errors,
            metrics=metrics,
            violations=violations,
            path=target,
        )

    @staticmethod
    def _exporter_config(
        prov: Provenance, pipeline: Sequence[StageConfig], ref: str
    ) -> StageConfig:
        """The pipeline's config for exporter ``ref`` (with its params), or a bare one."""
        try:
            wanted = prov.handle("exporter", ref).info.id
        except Unavailable as exc:
            raise ServiceError(exc.reason) from exc
        for config in pipeline:
            with contextlib.suppress(Unavailable):
                if prov.handle("exporter", config.component).info.id == wanted:
                    return config
        return StageConfig(component=ref)

    def export(
        self,
        store: ShowStore,
        exporters: Sequence[str] | None = None,
        *,
        allow_stale: bool = False,
        on_progress: Progress | None = None,
    ) -> list[ExportSummary]:
        show = store.load()
        prov = Provenance(show, self.registry)
        compiled_record = self._fresh_compiled(prov, store, allow_stale)
        configs = list(show.pipeline.exporters)
        if exporters:
            configs = [
                self._exporter_config(prov, show.pipeline.exporters, ref) for ref in exporters
            ]
        summaries = []
        ref = CompiledShowRef(path=store.path(compiled_record.path))
        for config in configs:
            try:
                handle, params = prov.stage("exporter", config)
            except Unavailable as exc:
                raise ServiceError(exc.reason) from exc
            slot = export_slot(handle.info.id)
            expectation = Expectation(producer_for(handle), params, None, ["compiled"])
            fp = fingerprint(
                expectation.producer, expectation.params, None, [compiled_record.fingerprint]
            )
            folder = store.new_file("exports", handle.info.id.replace(".", "_"), fp, "")
            folder.mkdir(parents=True)
            with self._work(store) as workdir:
                try:
                    result = handle.invoke(
                        {"show": ref, "out_dir": folder},
                        params,
                        seed=0,
                        workdir=workdir,
                        on_progress=on_progress,
                    )
                except PluginError as exc:
                    raise ServiceError(f"{handle.info.id}: {exc.message}") from exc
            record = new_record(
                kind="export_bundle",
                slot=slot,
                path=store.relative(folder),
                expectation=expectation,
                input_records=[compiled_record],
                content_hash=_dir_hash(folder),
            )
            show.artifacts[record.id] = record
            show.outputs.exports[handle.info.id] = record.id
            summaries.append(ExportSummary(handle.info.id, folder, list(result.files)))
        self._commit(store, show, "Export: " + ", ".join(s.component for s in summaries))
        return summaries
