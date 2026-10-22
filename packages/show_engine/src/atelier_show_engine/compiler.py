"""Compiling a show into trajectories: takeoff -> (transition, hold) per scene -> landing.

The compiler only orchestrates roles: animation primitives move the figures, the transition
planner assigns and flies the drones between them. Geometry the core owns itself is the
placement of figures and the vertical takeoff and landing.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from atelier_domain import ArtifactRecord, ModelCard, Scene, Show, StageConfig, derive_seed
from atelier_plugin_sdk import (
    AudioInfo,
    CompiledShow,
    PointSet,
    SceneMark,
    TransitionPlan,
    hex_to_linear,
    linear_to_srgb8,
)
from atelier_plugin_sdk.artifacts import SegmentKind
from atelier_show_engine.geometry import (
    ease,
    grid_positions,
    place,
    region_mask,
    smootherstep,
    vertical_move,
)
from atelier_show_engine.provenance import Expectation, Provenance, Unavailable
from atelier_show_engine.store import ShowStore

Array = npt.NDArray[np.float64]
Progress = Callable[[float, str], None]
MOVING_AT_END_MS = 0.05  # a figure still moving faster than this when its hold ends


class CompileError(Exception):
    pass


@dataclass
class CompileReport:
    duration_s: float
    frames: int
    drones: int
    budget_s: float
    segments: list[SceneMark]
    music_start_s: float | None = None
    music_duration_s: float | None = None
    warnings: list[str] = field(default_factory=list)

    def as_meta(self) -> dict[str, Any]:
        return {
            "duration_s": self.duration_s,
            "budget_s": self.budget_s,
            "music_start_s": self.music_start_s,
            "music_duration_s": self.music_duration_s,
            "warnings": self.warnings,
        }


class _Timeline:
    """Accumulates frames; every segment starts on the last frame of the previous one."""

    def __init__(self, positions: Array, colors: Array) -> None:
        self.positions: list[Array] = [positions[None]]
        self.colors: list[Array] = [colors[None]]
        self.marks = [SceneMark(kind="ground", title="On the ground", start_frame=0, end_frame=0)]
        self.last = 0

    @property
    def current(self) -> tuple[Array, Array]:
        return self.positions[-1][-1], self.colors[-1][-1]

    def append(
        self, positions: Array, colors: Array, kind: SegmentKind, scene: Scene | None, title: str
    ) -> None:
        new_positions, new_colors = positions[1:], colors[1:]
        if len(new_positions) == 0:
            return
        self.positions.append(new_positions)
        self.colors.append(new_colors)
        self.marks.append(
            SceneMark(
                kind=kind,
                scene_id=scene.id if scene else None,
                title=title,
                start_frame=self.last + 1,
                end_frame=self.last + len(new_positions),
            )
        )
        self.last += len(new_positions)


def _blend(start: Array, end: Array, frames: int) -> Array:
    weight = smootherstep(np.linspace(0.0, 1.0, frames))[:, None, None]
    result: Array = start[None] * (1 - weight) + end[None] * weight
    return result


def figure_colors(card: ModelCard, points: PointSet) -> Array:
    """Linear LED colour of each point of a figure."""
    if card.color_source == "texture" or (card.color_source == "palette" and not card.palette):
        return points.colors.astype(np.float64)
    palette = np.array([hex_to_linear(c) for c in card.palette or ["#ffffff"]], dtype=np.float64)
    if card.color_source == "solid":
        return np.tile(palette[0], (len(points), 1))
    distance = np.linalg.norm(points.colors[:, None, :] - palette[None], axis=2)
    result: Array = palette[np.argmin(distance, axis=1)]
    return result


class Compiler:
    def __init__(self, store: ShowStore, show: Show, provenance: Provenance, workdir: Path) -> None:
        self.store = store
        self.show = show
        self.prov = provenance
        self.workdir = workdir

    def _sample_hold(self, scene: Scene, local: Array) -> Array:
        """Figure-local positions for every frame of the hold, tracks applied in order."""
        fps = self.show.fps
        frames = max(2, round(scene.hold_s * fps) + 1)
        t_scene = np.arange(frames) / fps
        points = np.repeat(local[None], frames, axis=0)
        for index, track in enumerate(scene.animation.tracks if scene.animation else []):
            duration = track.duration_s or (scene.hold_s - track.start_s)
            if duration <= 0:
                raise CompileError(f"scene {scene.id}: track {index} starts after the hold ends")
            relative = t_scene - track.start_s
            if track.loop:
                t = np.where(relative < 0, 0.0, np.mod(relative, duration))
            else:
                t = np.clip(relative, 0.0, duration)
            progress = ease(track.easing, t / duration)
            handle, params = self.prov.stage(
                "animation_primitive", StageConfig(component=track.primitive, params=track.params)
            )
            points = handle.invoke(
                {
                    "points": points,
                    "mask": region_mask(track.region, local),
                    "t": t,
                    "progress": progress,
                },
                params,
                seed=derive_seed(self.show.seed, f"compiled.{scene.id}.track.{index}"),
                workdir=self.workdir,
            )
        return points

    def _plan(self, scene: Scene | None, start: Array, targets: Array, slot: str) -> TransitionPlan:
        show = self.show
        transition = scene.transition_in if scene else None
        if transition is not None and transition.planner:
            config = StageConfig(component=transition.planner, params=transition.params)
        else:
            config = show.pipeline.transition_planner
        handle, params = self.prov.stage("transition_planner", config)
        plan: TransitionPlan = handle.invoke(
            {
                "start": start,
                "targets": targets,
                "fleet": show.brief.fleet,
                "fps": show.fps,
                "duration_s": transition.duration_s if transition else None,
            },
            params,
            seed=derive_seed(show.seed, slot),
            workdir=self.workdir,
        )
        return plan

    def compile(
        self, *, allow_stale: bool = False, on_progress: Progress | None = None
    ) -> tuple[CompiledShow, CompileReport, Expectation, list[ArtifactRecord]]:
        show, prov = self.show, self.prov
        fleet, flight, fps = show.brief.fleet, show.flight, show.fps
        drones = fleet.drone_count
        try:
            expectation = prov.expectation("compiled")
        except Unavailable as exc:
            raise CompileError(exc.reason) from exc
        warnings: list[str] = []
        inputs: list[ArtifactRecord] = []
        for slot in expectation.inputs:
            record = prov.record(slot)
            if record is None:
                model_id, n = slot.split(".")[1], slot.split(".")[3]
                raise CompileError(
                    f"no points for model {model_id!r} at {n} drones; "
                    f"run `atelier points {model_id}`"
                )
            reason = prov.stale_reason(slot)
            if reason is not None:
                if not allow_stale:
                    raise CompileError(
                        f"{slot} is stale ({reason}); recompute it or pass --allow-stale"
                    )
                warnings.append(f"used stale {slot}: {reason}")
            inputs.append(record)
        for scene in show.program:
            count = prov.figure_drone_count(scene.model_id)
            if count != drones:
                raise CompileError(
                    f"model {scene.model_id!r} uses {count} of {drones} drones; figures with idle "
                    "drones are not supported yet"
                )
            if scene.animation and scene.animation.color_tracks:
                warnings.append(f"scene {scene.id}: colour tracks are ignored until phase 5")

        def progress(fraction: float, message: str) -> None:
            if on_progress:
                on_progress(fraction, message)

        idle = np.tile(hex_to_linear(flight.idle_color).astype(np.float64), (drones, 1))
        ground = grid_positions(drones, fleet.takeoff_grid_spacing_m, flight.grid_center_m)
        timeline = _Timeline(ground, idle)
        climb = vertical_move(
            ground,
            flight.takeoff_altitude_m,
            flight.takeoff_speed_ms or 0.8 * fleet.max_speed_up_ms,
            fleet.max_accel_ms2,
            fps,
        )
        timeline.append(
            climb, np.repeat(idle[None], len(climb), axis=0), "takeoff", None, "Takeoff"
        )
        takeoff_end_s = timeline.last / fps

        records = dict(zip(expectation.inputs, inputs, strict=True))
        for index, scene in enumerate(show.program):
            progress(index / (len(show.program) + 1), f"scene {scene.title}")
            card = show.models[scene.model_id]
            points = PointSet.load(
                self.store.path(records[f"models.{card.id}.points.{drones}"].path)
            )
            try:
                placement = place(
                    points.positions, card, scene, fleet, flight, show.brief.viewer_direction
                )
            except ValueError as exc:
                raise CompileError(f"scene {scene.id}: {exc}") from exc
            if placement.spacing_m < fleet.min_distance_m:
                warnings.append(
                    f"scene {scene.id}: figure spacing {placement.spacing_m:.2f} m is below the "
                    f"{fleet.min_distance_m:g} m minimum (target height too small?)"
                )
            world = placement.to_world(self._sample_hold(scene, placement.local))
            colors = figure_colors(card, points)
            start, start_colors = timeline.current
            plan = self._plan(scene, start, world[0], f"compiled.transition.{scene.id}")
            target_colors = colors[plan.assignment]
            timeline.append(
                plan.positions,
                _blend(start_colors, target_colors, len(plan.positions)),
                "transition",
                scene,
                f"To {scene.title}",
            )
            hold = world[:, plan.assignment]
            timeline.append(
                hold, np.repeat(target_colors[None], len(hold), axis=0), "hold", scene, scene.title
            )
            speed_at_end = float(np.linalg.norm(hold[-1] - hold[-2], axis=1).max() * fps)
            if speed_at_end > MOVING_AT_END_MS:
                warnings.append(
                    f"scene {scene.id}: figure still moves at {speed_at_end:.2f} m/s when the hold "
                    "ends; ease its last track or end it earlier"
                )

        start, start_colors = timeline.current
        landing_grid = ground + np.array([0.0, 0.0, flight.takeoff_altitude_m])
        back = self._plan(None, start, landing_grid, "compiled.transition.landing")
        timeline.append(
            back.positions,
            _blend(start_colors, idle, len(back.positions)),
            "transition",
            None,
            "Return",
        )
        start, _ = timeline.current
        descent = vertical_move(
            start,
            -flight.takeoff_altitude_m,
            flight.landing_speed_ms or 0.8 * fleet.max_speed_down_ms,
            fleet.max_accel_ms2,
            fps,
        )
        timeline.append(
            descent, np.repeat(idle[None], len(descent), axis=0), "landing", None, "Landing"
        )
        progress(1.0, "assembling")

        positions = np.concatenate(timeline.positions).astype(np.float32)
        colors_srgb = linear_to_srgb8(np.concatenate(timeline.colors))
        duration = (len(positions) - 1) / fps
        audio_info = None
        report = CompileReport(
            duration_s=duration,
            frames=len(positions),
            drones=drones,
            budget_s=show.brief.duration_s,
            segments=timeline.marks,
            warnings=warnings,
        )
        if show.audio is not None:
            offset = show.audio.offset_s if show.audio.offset_s is not None else takeoff_end_s
            audio_info = AudioInfo(
                path=self.store.path(show.audio.asset.path),
                offset_s=offset,
                duration_s=show.audio.duration_s,
            )
            report.music_start_s, report.music_duration_s = offset, show.audio.duration_s
            last_hold = (
                max((m.end_frame for m in timeline.marks if m.kind == "hold"), default=0) / fps
            )
            music_end = offset + show.audio.duration_s
            if not math.isclose(last_hold, music_end, abs_tol=1.0):
                warnings.append(
                    f"music ends at {music_end:.1f} s, the last scene at {last_hold:.1f} s"
                )
        if duration > show.brief.duration_s:
            warnings.append(
                f"show lasts {duration:.1f} s, over the {show.brief.duration_s:g} s budget"
            )
        compiled = CompiledShow(
            positions=positions,
            colors=colors_srgb,
            fps=fps,
            marks=timeline.marks,
            audio=audio_info,
            meta={
                "show_id": show.id,
                "title": show.brief.title,
                "fleet": fleet.model_dump(mode="json"),
                **report.as_meta(),
            },
        )
        return compiled, report, expectation, inputs
