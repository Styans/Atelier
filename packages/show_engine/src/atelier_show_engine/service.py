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

from atelier_domain import (
    AnimationSpec,
    AnimTrack,
    ArtifactRecord,
    AudioTrack,
    Brief,
    ModelCard,
    Scene,
    Show,
    StageConfig,
    TransitionSpec,
    Vec3,
    ViewerDirection,
    fingerprint,
    fleet_from_preset,
)
from atelier_domain.ids import new_show_id, unique_id
from atelier_plugin_host import PluginRegistry
from atelier_plugin_sdk import (
    CompiledShowRef,
    MeshRef,
    PluginError,
    PointSet,
    RuleResult,
    Violation,
)
from atelier_plugin_sdk.roles import RoleName
from atelier_show_engine.compiler import CompileError, Compiler, CompileReport
from atelier_show_engine.config import AtelierConfig
from atelier_show_engine.importing import UpAxis, copy_asset, load_mesh_file, probe_audio
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
from atelier_show_engine.store import ShowStore, StoreError

Progress = Callable[[float, str], None]
SINGLE_STAGE_ROLES: tuple[RoleName, ...] = ("point_distributor", "transition_planner")


class ServiceError(Exception):
    pass


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


class ShowService:
    def __init__(self, config: AtelierConfig, registry: PluginRegistry | None = None) -> None:
        self.config = config
        self._registry = registry

    @property
    def registry(self) -> PluginRegistry:
        if self._registry is None:
            self._registry = PluginRegistry.discover(
                self.config.plugin_dirs,
                config=self.config.host_config(),
                disabled=self.config.plugins.disabled,
                cache_path=self.config.cache_dir / "plugins.json",
                log_dir=self.config.cache_dir / "logs",
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
        return store.commit(show, f"Fleet preset {name}")

    def set_fleet(self, store: ShowStore, **values: Any) -> Show:
        show = store.load()
        data = show.brief.fleet.model_dump() | {k: v for k, v in values.items() if v is not None}
        data["preset"] = None if values.keys() - {"drone_count"} else data["preset"]
        show.brief.fleet = type(show.brief.fleet).model_validate(data)
        return store.commit(
            show, "Fleet: " + ", ".join(f"{k}={v}" for k, v in values.items() if v is not None)
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
        return store.commit(show, f"Music {path.name} ({duration:.1f} s)")

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
        return store.commit(show, f"Add model {name} from {source.name}"), model_id

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
        store.commit(show, f"Points for {model_id}: {n} by {handle.info.id}")
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
        store.commit(show, f"Add scene {scene.title}")
        return scene

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
        return store.commit(show, f"Pipeline {role}: {component} {json.dumps(params)}")

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
        return store.commit(show, f"Pipeline {which}: {', '.join(components)}")

    # --- status ---

    def stale_report(self, store: ShowStore) -> dict[str, str | None]:
        return Provenance(store.load(), self.registry).report()

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
        store.commit(show, f"Compile: {report.frames} frames, {report.duration_s:.1f} s")
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
        store.commit(show, f"Validate: {errors} errors")
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
        store.commit(show, "Export: " + ", ".join(s.component for s in summaries))
        return summaries
