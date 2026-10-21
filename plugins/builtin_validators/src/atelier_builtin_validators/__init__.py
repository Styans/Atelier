"""Built-in safety rules over the compiled trajectories.

Each rule reports *time windows*: consecutive frames where the rule is broken become one
violation listing every drone involved and the worst value. Drone indices are zero-based;
"Drone k" in Blender and Skybrush is index k - 1.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field
from scipy.spatial import cKDTree

from atelier_domain import FleetSpec
from atelier_plugin_sdk import (
    CompiledShow,
    CompiledShowRef,
    Health,
    Plugin,
    RuleResult,
    RunContext,
    ValidatorRule,
    Violation,
)

Array = npt.NDArray[np.float64]
SPEED_TOLERANCE = 1e-3  # m/s; finite differences of float32 positions are not exact
MAX_VIOLATIONS = 500


def windows(flags: npt.NDArray[np.bool_]) -> Iterator[tuple[int, int]]:
    """Inclusive (start, end) index runs where ``flags`` is true."""
    padded = np.concatenate([[False], flags, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    for start, stop in zip(edges[::2], edges[1::2], strict=True):
        yield int(start), int(stop) - 1


def drone_list(drones: npt.NDArray[np.int64], limit: int = 12) -> str:
    names = [f"Drone {d + 1}" for d in drones[:limit]]
    more = f" and {len(drones) - limit} more" if len(drones) > limit else ""
    return ", ".join(names) + more


class _Collector:
    def __init__(self, rule: str, show: CompiledShow, limit: float, unit: str) -> None:
        self.rule, self.show, self.limit, self.unit = rule, show, limit, unit
        self.violations: list[Violation] = []
        self.truncated = 0

    def add(
        self, start: int, end: int, drones: npt.NDArray[np.int64], value: float, what: str
    ) -> None:
        if len(self.violations) >= MAX_VIOLATIONS:
            self.truncated += 1
            return
        fps = self.show.fps
        self.violations.append(
            Violation(
                rule=self.rule,
                frame_start=start,
                frame_end=end,
                t_start_s=start / fps,
                t_end_s=end / fps,
                drones=[int(d) for d in drones],
                value=value,
                limit=self.limit,
                unit=self.unit,
                message=(
                    f"{what} {value:.2f} {self.unit} (limit {self.limit:g}) at "
                    f"{start / fps:.2f}-{end / fps:.2f} s: {drone_list(drones)}"
                ),
            )
        )

    def result(self, metrics: dict[str, float]) -> RuleResult:
        return RuleResult(violations=self.violations, metrics=metrics, truncated=self.truncated)


class MinDistanceParams(BaseModel):
    min_distance_m: float | None = Field(None, gt=0, description="None = the fleet's min distance")
    ignore_below_m: float | None = Field(
        None, ge=0, description="Skip drones below this height; None = fleet altitude_min_m"
    )


class MinDistance(ValidatorRule[MinDistanceParams]):
    """No two drones closer than the minimum distance, frame by frame (KD-tree)."""

    name = "min_distance"
    version = "0.1.0"
    Params = MinDistanceParams

    def check(
        self, ctx: RunContext, show: CompiledShowRef, fleet: FleetSpec, params: MinDistanceParams
    ) -> RuleResult:
        compiled = show.load()
        limit = params.min_distance_m or fleet.min_distance_m
        floor = fleet.altitude_min_m if params.ignore_below_m is None else params.ignore_below_m
        positions = compiled.positions.astype(np.float64)
        frames = len(positions)
        nearest = np.full(frames, np.inf)
        close: list[npt.NDArray[np.int64]] = [np.empty(0, dtype=np.int64)] * frames
        closest = np.full(frames, np.inf)
        previous: tuple[float, npt.NDArray[np.int64], float] | None = None
        for f in range(frames):
            if previous is not None and np.array_equal(positions[f], positions[f - 1]):
                nearest[f], close[f], closest[f] = previous
                continue
            active = np.flatnonzero(positions[f, :, 2] >= floor)
            if len(active) >= 2:
                points = positions[f, active]
                tree = cKDTree(points)
                distances, _ = tree.query(points, k=2)
                nearest[f] = distances[:, 1].min()
                pairs = tree.query_pairs(r=limit, output_type="ndarray")
                if len(pairs):
                    d = np.linalg.norm(points[pairs[:, 0]] - points[pairs[:, 1]], axis=1)
                    bad = pairs[d < limit - 1e-6]
                    if len(bad):
                        close[f] = np.unique(active[bad])
                        closest[f] = d[d < limit - 1e-6].min()
            previous = (nearest[f], close[f], closest[f])
            if f % 500 == 0:
                ctx.progress(f / frames, "checking distances")
        collector = _Collector(self.name, compiled, limit, "m")
        for start, end in windows(np.isfinite(closest)):
            drones = np.unique(np.concatenate(close[start : end + 1]))
            collector.add(start, end, drones, float(closest[start : end + 1].min()), "distance")
        finite = np.isfinite(nearest)
        metrics = {}
        if finite.any():
            worst = int(np.argmin(np.where(finite, nearest, np.inf)))
            metrics = {
                "min_distance_m": float(nearest[worst]),
                "min_distance_at_s": worst / compiled.fps,
            }
        return collector.result(metrics)


def velocities(compiled: CompiledShow) -> Array:
    """Velocity between frame f and f + 1, shape (F - 1, D, 3)."""
    result: Array = np.diff(compiled.positions.astype(np.float64), axis=0) * compiled.fps
    return result


class SpeedParams(BaseModel):
    pass


class Speed(ValidatorRule[SpeedParams]):
    """Horizontal, climb and descent speed limits of the fleet."""

    name = "speed"
    version = "0.1.0"
    Params = SpeedParams

    def check(
        self, ctx: RunContext, show: CompiledShowRef, fleet: FleetSpec, params: SpeedParams
    ) -> RuleResult:
        compiled = show.load()
        v = velocities(compiled)
        kinds = {
            "horizontal speed": (np.linalg.norm(v[..., :2], axis=2), fleet.max_speed_h_ms),
            "climb speed": (v[..., 2], fleet.max_speed_up_ms),
            "descent speed": (-v[..., 2], fleet.max_speed_down_ms),
        }
        violations: list[Violation] = []
        truncated = 0
        metrics: dict[str, float] = {}
        for what, (values, limit) in kinds.items():
            collector = _Collector(self.name, compiled, limit, "m/s")
            over = values > limit + SPEED_TOLERANCE
            for start, end in windows(over.any(axis=1)):
                drones = np.flatnonzero(over[start : end + 1].any(axis=0))
                collector.add(start, end + 1, drones, float(values[start : end + 1].max()), what)
            violations += collector.violations
            truncated += collector.truncated
            metrics[f"max_{what.replace(' ', '_')}_ms"] = float(values.max(initial=0.0))
        return RuleResult(violations=violations, metrics=metrics, truncated=truncated)


class AccelParams(BaseModel):
    tolerance_ms2: float = Field(0.05, ge=0, description="Allowed excess over the limit")


class Accel(ValidatorRule[AccelParams]):
    """Acceleration limit; also catches jumps where a motion stops abruptly between scenes."""

    name = "accel"
    version = "0.1.0"
    Params = AccelParams

    def check(
        self, ctx: RunContext, show: CompiledShowRef, fleet: FleetSpec, params: AccelParams
    ) -> RuleResult:
        compiled = show.load()
        a = np.linalg.norm(np.diff(velocities(compiled), axis=0) * compiled.fps, axis=2)
        limit = fleet.max_accel_ms2
        collector = _Collector(self.name, compiled, limit, "m/s^2")
        over = a > limit + params.tolerance_ms2
        for start, end in windows(over.any(axis=1)):
            drones = np.flatnonzero(over[start : end + 1].any(axis=0))
            collector.add(
                start + 1, end + 1, drones, float(a[start : end + 1].max()), "acceleration"
            )
        return collector.result({"max_acceleration_ms2": float(a.max(initial=0.0))})


class AltitudeParams(BaseModel):
    pass


class Altitude(ValidatorRule[AltitudeParams]):
    """Ceiling at all times; floor everywhere except on the ground, takeoff and landing."""

    name = "altitude"
    version = "0.1.0"
    Params = AltitudeParams

    def check(
        self, ctx: RunContext, show: CompiledShowRef, fleet: FleetSpec, params: AltitudeParams
    ) -> RuleResult:
        compiled = show.load()
        z = compiled.positions[..., 2].astype(np.float64)
        ceiling = _Collector(self.name, compiled, fleet.altitude_max_m, "m")
        too_high = z > fleet.altitude_max_m + 1e-6
        for start, end in windows(too_high.any(axis=1)):
            drones = np.flatnonzero(too_high[start : end + 1].any(axis=0))
            ceiling.add(start, end, drones, float(z[start : end + 1].max()), "altitude")
        exempt = np.zeros(len(z), dtype=bool)
        for mark in compiled.marks:
            if mark.kind in ("ground", "takeoff", "landing"):
                exempt[mark.start_frame : mark.end_frame + 1] = True
        floor = _Collector(self.name, compiled, fleet.altitude_min_m, "m")
        too_low = (z < fleet.altitude_min_m - 1e-6) & ~exempt[:, None]
        for start, end in windows(too_low.any(axis=1)):
            drones = np.flatnonzero(too_low[start : end + 1].any(axis=0))
            floor.add(start, end, drones, float(z[start : end + 1].min()), "altitude")
        flying = z[~exempt] if (~exempt).any() else z
        return RuleResult(
            violations=ceiling.violations + floor.violations,
            metrics={
                "max_altitude_m": float(z.max()),
                "min_flying_altitude_m": float(flying.min()),
            },
            truncated=ceiling.truncated + floor.truncated,
        )


plugin = Plugin(components=[MinDistance(), Speed(), Accel(), Altitude()], health_check=Health.ok)
