"""Which artifacts are current, and why the others are stale (ADR-012).

Every artifact fills a *slot* (``models.<id>.mesh``, ``models.<id>.points.<n>``, ``compiled``,
``validation``, ``exports.<component>``). For each slot we can say what *should* have produced
it now: component, plugin version, normalized parameters, seed and the slots it reads. The
fingerprint of that expectation is compared with the one recorded when the artifact was made;
staleness is computed, never stored, so it cannot get out of sync.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from atelier_domain import (
    ArtifactKind,
    ArtifactRecord,
    Producer,
    Show,
    StageConfig,
    canonical_json,
    derive_seed,
    fingerprint,
)
from atelier_plugin_host import ComponentHandle, ComponentNotFound, PluginRegistry
from atelier_plugin_sdk import API_VERSION, PluginError
from atelier_plugin_sdk.roles import RoleName

MAX_CHANGES = 4
_MISSING = object()
# Wrappers the expectations put around a component's own parameters; the reader knows them
# as plain parameter names ("relax_iterations", not "component.relax_iterations").
_WRAPPERS = ("component",)


def _short(value: Any) -> str:
    if value is _MISSING:
        return "none"
    text = canonical_json(value)
    return text if len(text) <= 40 else f"{text[:37]}..."


def param_changes(old: Any, new: Any, path: str = "") -> list[str]:
    """``"relax_iterations: 20 -> 40"`` for every leaf that differs, in key order."""
    if isinstance(old, dict) and isinstance(new, dict):
        changes = []
        for key in sorted(old.keys() | new.keys()):
            inner = key if not path or path in _WRAPPERS else f"{path}.{key}"
            changes += param_changes(old.get(key, _MISSING), new.get(key, _MISSING), inner)
        return changes
    if old == new:
        return []
    return [f"{path or 'parameters'}: {_short(old)} -> {_short(new)}"]


def core_producer(component: str, role: str, version: str) -> Producer:
    """Producer of artifacts made by the core; bump ``version`` when the output changes."""
    return Producer(
        component=component,
        role=role,
        plugin_id="core",
        plugin_version=version,
        component_version=version,
        api_version=API_VERSION,
    )


IMPORT_MESH = core_producer("core.import_mesh", "import", "1")
COMPILER = core_producer("core.compile", "compile", "1")
VALIDATION = core_producer("core.validate", "validate", "1")


def producer_for(handle: ComponentHandle) -> Producer:
    manifest = handle.plugin.manifest
    return Producer(
        component=handle.info.id,
        role=handle.info.role,
        plugin_id=manifest.id,
        plugin_version=manifest.version,
        component_version=handle.info.version,
        api_version=manifest.api_version,
    )


class Unavailable(Exception):
    """The expectation of a slot cannot be built (missing input, unknown component...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Expectation:
    producer: Producer
    params: dict[str, Any]
    seed: int | None
    inputs: list[str]


def points_slot(model_id: str, n: int) -> str:
    return f"models.{model_id}.points.{n}"


def mesh_slot(model_id: str) -> str:
    return f"models.{model_id}.mesh"


def export_slot(component_id: str) -> str:
    return f"exports.{component_id}"


def new_record(
    *,
    kind: ArtifactKind,
    slot: str,
    path: str,
    expectation: Expectation,
    input_records: Sequence[ArtifactRecord],
    content_hash: str,
) -> ArtifactRecord:
    """A record for an artifact produced from ``input_records`` (the files actually used)."""
    return ArtifactRecord(
        id=f"a_{uuid.uuid4().hex[:12]}",
        kind=kind,
        path=path,
        slot=slot,
        producer=expectation.producer,
        params=expectation.params,
        seed=expectation.seed,
        inputs=[r.id for r in input_records],
        fingerprint=fingerprint(
            expectation.producer,
            expectation.params,
            expectation.seed,
            [r.fingerprint for r in input_records],
        ),
        content_hash=content_hash,
        created_at=dt.datetime.now(dt.UTC),
    )


class Provenance:
    def __init__(self, show: Show, registry: PluginRegistry) -> None:
        self.show = show
        self.registry = registry
        self._fingerprints: dict[str, str] = {}
        self._handles: dict[tuple[str, str], ComponentHandle] = {}
        self._normalized: dict[tuple[str, str, str], dict[str, Any]] = {}

    # --- pointers ---

    def pointer(self, slot: str) -> str | None:
        parts = slot.split(".")
        show = self.show
        if parts[0] == "models" and len(parts) >= 3 and parts[1] in show.models:
            assets = show.models[parts[1]].assets
            if parts[2] == "mesh":
                return assets.mesh
            if parts[2] == "points" and len(parts) == 4:
                return assets.point_sets.get(int(parts[3]))
        if slot == "compiled":
            return show.outputs.compiled
        if slot == "validation":
            return show.outputs.validation
        if parts[0] == "exports":
            return show.outputs.exports.get(slot.removeprefix("exports."))
        return None

    def record(self, slot: str) -> ArtifactRecord | None:
        artifact_id = self.pointer(slot)
        return self.show.artifacts.get(artifact_id) if artifact_id else None

    def slots(self) -> list[str]:
        """All slots that currently point at an artifact."""
        slots = []
        for model_id, card in self.show.models.items():
            if card.assets.mesh:
                slots.append(mesh_slot(model_id))
            slots += [points_slot(model_id, n) for n in sorted(card.assets.point_sets)]
        if self.show.outputs.compiled:
            slots.append("compiled")
        if self.show.outputs.validation:
            slots.append("validation")
        slots += [export_slot(c) for c in sorted(self.show.outputs.exports)]
        return slots

    # --- components ---

    def handle(self, role: RoleName, ref: str) -> ComponentHandle:
        key = (role, ref)
        if key not in self._handles:
            try:
                self._handles[key] = self.registry.resolve(role, ref)
            except ComponentNotFound as exc:
                raise Unavailable(str(exc)) from exc
        return self._handles[key]

    def normalized(self, handle: ComponentHandle, params: dict[str, Any]) -> dict[str, Any]:
        key = (handle.info.role, handle.info.id, canonical_json(params))
        if key not in self._normalized:
            try:
                self._normalized[key] = handle.normalize_params(params)
            except PluginError as exc:
                raise Unavailable(f"{handle.info.id}: {exc.message}") from exc
        return self._normalized[key]

    def stage(self, role: RoleName, config: StageConfig) -> tuple[ComponentHandle, dict[str, Any]]:
        handle = self.handle(role, config.component)
        return handle, self.normalized(handle, config.params)

    # --- expectations ---

    def expectation(self, slot: str) -> Expectation:
        parts = slot.split(".")
        if parts[0] == "models" and len(parts) == 4 and parts[2] == "points":
            return self._points(parts[1], int(parts[3]), slot)
        if slot == "compiled":
            return self._compiled()
        if slot == "validation":
            return self._validation()
        if parts[0] == "exports":
            return self._export(slot.removeprefix("exports."), slot)
        raise Unavailable(f"no expectation for slot {slot!r}")

    def point_stage(self, model_id: str) -> StageConfig:
        card = self.show.models[model_id]
        return (
            card.pipeline_overrides.get("point_distributor") or self.show.pipeline.point_distributor
        )

    def _points(self, model_id: str, n: int, slot: str) -> Expectation:
        if model_id not in self.show.models:
            raise Unavailable(f"model {model_id!r} no longer exists")
        handle, params = self.stage("point_distributor", self.point_stage(model_id))
        return Expectation(
            producer=producer_for(handle),
            params={"component": params, "n": n},
            seed=derive_seed(self.show.seed, slot),
            inputs=[mesh_slot(model_id)],
        )

    def figure_drone_count(self, model_id: str) -> int:
        return self.show.models[model_id].drone_count or self.show.brief.fleet.drone_count

    def _compiled(self) -> Expectation:
        show = self.show
        if not show.program:
            raise Unavailable("the program has no scenes")
        planners: dict[str, Any] = {}
        primitives: dict[str, Any] = {}
        tracks: list[list[dict[str, Any]]] = []
        default = show.pipeline.transition_planner
        for scene in show.program:
            transition = scene.transition_in
            config = (
                StageConfig(component=transition.planner, params=transition.params)
                if transition.planner
                else default
            )
            handle, params = self.stage("transition_planner", config)
            planners[scene.id] = {"producer": producer_for(handle).model_dump(), "params": params}
            scene_tracks = []
            for track in scene.animation.tracks if scene.animation else []:
                handle, params = self.stage(
                    "animation_primitive",
                    StageConfig(component=track.primitive, params=track.params),
                )
                primitives[handle.info.id] = producer_for(handle).model_dump()
                scene_tracks.append(params)
            tracks.append(scene_tracks)
        handle, params = self.stage("transition_planner", default)
        planners["__default__"] = {"producer": producer_for(handle).model_dump(), "params": params}
        model_ids = list(dict.fromkeys(scene.model_id for scene in show.program))
        audio = show.audio
        return Expectation(
            producer=COMPILER,
            params={
                "fps": show.fps,
                "fleet": show.brief.fleet.model_dump(mode="json"),
                "viewer": show.brief.viewer_direction,
                "flight": show.flight.model_dump(mode="json"),
                "program": [s.model_dump(mode="json") for s in show.program],
                "track_params": tracks,
                "planners": planners,
                "primitives": primitives,
                "models": {
                    m: show.models[m].model_dump(
                        mode="json",
                        include={
                            "target_height_m",
                            "facing",
                            "color_source",
                            "palette",
                            "drone_count",
                        },
                    )
                    for m in model_ids
                },
                "audio": None
                if audio is None
                else {
                    "hash": audio.asset.content_hash,
                    "duration_s": audio.duration_s,
                    "offset_s": audio.offset_s,
                },
            },
            seed=derive_seed(show.seed, "compiled"),
            inputs=[points_slot(m, self.figure_drone_count(m)) for m in model_ids],
        )

    def _validation(self) -> Expectation:
        validators = []
        for config in self.show.pipeline.validators:
            handle, params = self.stage("validator_rule", config)
            validators.append({"producer": producer_for(handle).model_dump(), "params": params})
        return Expectation(
            producer=VALIDATION,
            params={
                "validators": validators,
                "fleet": self.show.brief.fleet.model_dump(mode="json"),
            },
            seed=None,
            inputs=["compiled"],
        )

    def exporter_config(self, component_id: str) -> StageConfig:
        for config in self.show.pipeline.exporters:
            if self.handle("exporter", config.component).info.id == component_id:
                return config
        raise Unavailable(f"exporter {component_id!r} is no longer in the pipeline")

    def _export(self, component_id: str, slot: str) -> Expectation:
        handle, params = self.stage("exporter", self.exporter_config(component_id))
        return Expectation(
            producer=producer_for(handle), params=params, seed=None, inputs=["compiled"]
        )

    # --- fingerprints and staleness ---

    def expected_fingerprint(self, slot: str) -> str:
        if slot in self._fingerprints:
            return self._fingerprints[slot]
        if slot.endswith(".mesh"):
            # Imported meshes are inputs chosen by the user: whatever is current is expected.
            record = self.record(slot)
            if record is None:
                raise Unavailable(f"{slot} is missing")
            value = record.fingerprint
        else:
            exp = self.expectation(slot)
            value = fingerprint(
                exp.producer,
                exp.params,
                exp.seed,
                [self.expected_fingerprint(s) for s in exp.inputs],
            )
        self._fingerprints[slot] = value
        return value

    def stale_reason(self, slot: str) -> str | None:
        """None if the artifact in ``slot`` is current, otherwise why it is not."""
        record = self.record(slot)
        if record is None:
            return "missing"
        try:
            if record.fingerprint == self.expected_fingerprint(slot):
                return None
            exp = self.expectation(slot)
        except Unavailable as exc:
            return exc.reason
        if record.producer.component != exp.producer.component:
            return f"component changed: {record.producer.component} -> {exp.producer.component}"
        if record.producer != exp.producer:
            return (
                f"{exp.producer.plugin_id} updated: {record.producer.plugin_version} -> "
                f"{exp.producer.plugin_version}"
            )
        if record.params != exp.params:
            changed = param_changes(record.params, exp.params)
            more = f" and {len(changed) - MAX_CHANGES} more" if len(changed) > MAX_CHANGES else ""
            return f"parameters changed: {'; '.join(changed[:MAX_CHANGES])}{more}"
        if record.seed != exp.seed:
            return "seed changed"
        for input_slot in exp.inputs:
            reason = self.stale_reason(input_slot)
            if reason is not None:
                return f"input {input_slot} is stale ({reason})"
            current = self.record(input_slot)
            if current is None or current.id not in record.inputs:
                return f"input {input_slot} was replaced"
        return "inputs changed"

    def report(self) -> dict[str, str | None]:
        return {slot: self.stale_reason(slot) for slot in self.slots()}

    def require_fresh(self, slots: Sequence[str]) -> list[ArtifactRecord]:
        """The current records of ``slots``; raises ``Unavailable`` if one is stale or missing."""
        records = []
        for slot in slots:
            reason = self.stale_reason(slot)
            record = self.record(slot)
            if reason is not None or record is None:
                raise Unavailable(f"{slot}: {reason or 'missing'}")
            records.append(record)
        return records
