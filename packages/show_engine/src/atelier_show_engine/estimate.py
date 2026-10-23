"""How long the program will run, before it is compiled (ADR-038).

The estimate walks the program the way the compiler does: takeoff, then for every scene a
transition and a hold, then the return and the landing. Takeoff, landing and holds use the
compiler's own frame counts, so they are exact. A transition is the default planner's flight
(optimal assignment, synchronized quintic within the fleet limits, its free-duration margin)
between the figures placed as the compiler places them: from their points when they exist,
otherwise from a flat stand-in of the same drone count. Animation during holds is ignored,
and so is a scene's own planner, so transitions are an estimate until the show is compiled.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist

from atelier_domain import FleetSpec, Show
from atelier_show_engine.geometry import (
    PEAK_ACCELERATION,
    PEAK_VELOCITY,
    grid_positions,
    hold_frames,
    place,
    vertical_frames,
)

Array = npt.NDArray[np.float64]
# Figure positions of a model at a drone count, or None when there are no points yet.
PointsLoader = Callable[[str, int], Array | None]

# The default planner's (builtin.hungarian_direct) free duration: safe duration x margin.
PLANNER_MARGIN = 1.15
PLANNER_MIN_S = 2.0
# Two stand-ins are the same grid, so nobody would move between them; real figures differ and
# drones re-arrange. Floor: some drones fly this share of the figure's size up, down and
# sideways. Calibrated on 150-drone shows (conservative preset): 9.3 s compiled, ~9 s here.
RESHAPE_SHARE = 0.18

SegmentKind = Literal["takeoff", "transition", "hold", "landing"]
# exact: what the compiler will make; points/proxy: a transition flown between the figure's
# points or a stand-in; fixed: the scene sets the transition length itself.
Basis = Literal["exact", "points", "proxy", "fixed"]


@dataclass(frozen=True)
class Segment:
    kind: SegmentKind
    scene_id: str | None
    title: str
    start_s: float
    duration_s: float
    basis: Basis

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


@dataclass
class TimelineEstimate:
    segments: list[Segment]
    budget_s: float
    compiled: bool = False  # taken from the current compiled show, not estimated
    music_start_s: float | None = None
    music_end_s: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_s(self) -> float:
        return self.segments[-1].end_s if self.segments else 0.0

    @property
    def over_budget_s(self) -> float:
        return max(0.0, self.total_s - self.budget_s)

    def scene_s(self, scene_id: str) -> float:
        """Transition plus hold of one scene."""
        return sum(s.duration_s for s in self.segments if s.scene_id == scene_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "segments": [
                {
                    "kind": s.kind,
                    "scene_id": s.scene_id,
                    "title": s.title,
                    "start_s": s.start_s,
                    "duration_s": s.duration_s,
                    "basis": s.basis,
                }
                for s in self.segments
            ],
            "total_s": self.total_s,
            "budget_s": self.budget_s,
            "over_budget_s": self.over_budget_s,
            "compiled": self.compiled,
            "music_start_s": self.music_start_s,
            "music_end_s": self.music_end_s,
            "notes": self.notes,
        }


def flight_duration(delta: Array, fleet: FleetSpec) -> float:
    """Shortest synchronized quintic flight over ``delta[D, 3]`` within the fleet limits."""
    horizontal = np.linalg.norm(delta[:, :2], axis=1).max(initial=0.0)
    up = np.clip(delta[:, 2], 0, None).max(initial=0.0)
    down = np.clip(-delta[:, 2], 0, None).max(initial=0.0)
    distance = np.linalg.norm(delta, axis=1).max(initial=0.0)
    return float(
        max(
            PEAK_VELOCITY * horizontal / fleet.max_speed_h_ms,
            PEAK_VELOCITY * up / fleet.max_speed_up_ms,
            PEAK_VELOCITY * down / fleet.max_speed_down_ms,
            math.sqrt(PEAK_ACCELERATION * distance / fleet.max_accel_ms2),
        )
    )


def transition_s(start: Array, targets: Array, fleet: FleetSpec, fps: int) -> float:
    rows, cols = linear_sum_assignment(cdist(start, targets, "sqeuclidean"))
    delta = targets[cols[np.argsort(rows)]] - start
    duration = max(flight_duration(delta, fleet) * PLANNER_MARGIN, PLANNER_MIN_S)
    return math.ceil(duration * fps) / fps


def reshape_s(a: Array, b: Array, fleet: FleetSpec, fps: int) -> float:
    """The floor for a transition between figures when one of them is a stand-in."""
    size = max(float(np.ptp(a, axis=0).max()), float(np.ptp(b, axis=0).max()))
    r = RESHAPE_SHARE * size
    duration = max(
        flight_duration(np.array([[r, 0, r], [0, 0, -r]]), fleet) * PLANNER_MARGIN, PLANNER_MIN_S
    )
    return math.ceil(duration * fps) / fps


def stand_in(count: int) -> Array:
    """A flat, near-square grid facing the audience: a figure without points yet."""
    columns = math.ceil(math.sqrt(count))
    index = np.arange(count)
    return np.column_stack(
        [index % columns, np.zeros(count), index // columns],
    ).astype(np.float64)


def estimate_timeline(show: Show, load_points: PointsLoader) -> TimelineEstimate:
    fleet, flight, fps = show.brief.fleet, show.flight, show.fps
    drones = fleet.drone_count
    segments: list[Segment] = []
    notes: list[str] = []
    clock = 0.0

    def add(
        kind: SegmentKind, scene_id: str | None, title: str, seconds: float, basis: Basis
    ) -> None:
        nonlocal clock
        segments.append(Segment(kind, scene_id, title, clock, seconds, basis))
        clock += seconds

    up = vertical_frames(
        flight.takeoff_altitude_m,
        flight.takeoff_speed_ms or 0.8 * fleet.max_speed_up_ms,
        fleet.max_accel_ms2,
        fps,
    )
    add("takeoff", None, "Takeoff", (up - 1) / fps, "exact")
    takeoff_end = clock
    ground = grid_positions(drones, fleet.takeoff_grid_spacing_m, flight.grid_center_m)
    landing_grid = ground + np.array([0.0, 0.0, flight.takeoff_altitude_m])
    current = landing_grid  # where the drones are...
    current_basis: Basis = "exact"  # ...and how well we know it

    for scene in show.program:
        card = show.models.get(scene.model_id)
        if card is None:
            notes.append(f"scene {scene.id}: model {scene.model_id!r} is missing")
            continue
        points = load_points(card.id, drones)
        basis: Basis = "points" if points is not None else "proxy"
        try:
            placement = place(
                points if points is not None else stand_in(drones),
                card,
                scene,
                fleet,
                flight,
                show.brief.viewer_direction,
            )
        except ValueError as exc:
            notes.append(f"scene {scene.id}: {exc}")
            continue
        figure = placement.to_world(placement.local)
        title = f"To {scene.title}"
        if scene.transition_in.duration_s is not None:
            # The planner flies a requested length as it is (frames = round(d * fps) + 1).
            seconds = max(1, round(scene.transition_in.duration_s * fps)) / fps
            add("transition", scene.id, title, seconds, "fixed")
        else:
            flown = transition_s(current, figure, fleet, fps)
            worst: Basis = "proxy" if "proxy" in (basis, current_basis) else "points"
            if worst == "proxy" and current_basis != "exact":  # figure to figure, not takeoff
                flown = max(flown, reshape_s(current, figure, fleet, fps))
            add("transition", scene.id, title, flown, worst)
        add("hold", scene.id, scene.title, (hold_frames(scene.hold_s, fps) - 1) / fps, "exact")
        current, current_basis = figure, basis

    add(
        "transition", None, "Return", transition_s(current, landing_grid, fleet, fps), current_basis
    )
    down = vertical_frames(
        flight.takeoff_altitude_m,
        flight.landing_speed_ms or 0.8 * fleet.max_speed_down_ms,
        fleet.max_accel_ms2,
        fps,
    )
    add("landing", None, "Landing", (down - 1) / fps, "exact")

    estimate = TimelineEstimate(segments, show.brief.duration_s, notes=notes)
    if show.audio is not None:
        start = show.audio.offset_s if show.audio.offset_s is not None else takeoff_end
        estimate.music_start_s, estimate.music_end_s = start, start + show.audio.duration_s
    return estimate
