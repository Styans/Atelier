"""Placement of figures, easing, regions and vertical flight: deterministic numpy."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.spatial import cKDTree

from atelier_domain import (
    Easing,
    FleetSpec,
    FlightSettings,
    ModelCard,
    Region,
    RegionAll,
    RegionBox,
    RegionPlaneSplit,
    Scene,
    ViewerDirection,
)

Array = npt.NDArray[np.float64]

# Quintic smootherstep: zero velocity and acceleration at both ends.
PEAK_VELOCITY = 15 / 8
PEAK_ACCELERATION = 10 / math.sqrt(3)


def smootherstep(p: Array) -> Array:
    result: Array = p**3 * (10 - 15 * p + 6 * p**2)
    return result


def ease(kind: Easing, p: Array) -> Array:
    p = np.clip(p, 0.0, 1.0)
    if kind == "ease_in_out":
        return smootherstep(p)
    if kind == "sine":
        result: Array = 0.5 - 0.5 * np.cos(np.pi * p)
        return result
    return p


def nearest_spacing(points: Array) -> float:
    if len(points) < 2:
        return math.inf
    distances, _ = cKDTree(points).query(points, k=2)
    return float(distances[:, 1].min())


def min_safe_scale(points: Array, min_distance: float) -> float:
    """Scale at which the closest two points are exactly ``min_distance`` apart."""
    spacing = nearest_spacing(points)
    if spacing == 0:
        raise ValueError("two points coincide; the figure cannot be scaled apart")
    return min_distance / spacing if math.isfinite(spacing) else 1.0


def _rot_x(deg: float) -> Array:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _rot_y(deg: float) -> Array:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rot_z(deg: float) -> Array:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def euler_xyz(degrees: tuple[float, float, float]) -> Array:
    """Blender's XYZ Euler order: rotate about X, then Y, then Z (world axes)."""
    result: Array = _rot_z(degrees[2]) @ _rot_y(degrees[1]) @ _rot_x(degrees[0])
    return result


# A figure's front looks at -Y; turn it towards the audience.
AUDIENCE_TURN_DEG: dict[ViewerDirection, float] = {"-Y": 0.0, "+Y": 180.0, "+X": 90.0, "-X": -90.0}


@dataclass
class Placement:
    local: Array  # figure-local rest positions: centred on the bounding box, scaled
    rotation: Array  # local -> world
    translation: Array
    scale: float
    spacing_m: float  # nearest-neighbour spacing of the placed figure

    def to_world(self, local: Array) -> Array:
        result: Array = local @ self.rotation.T + self.translation
        return result


def place(
    positions: Array,
    card: ModelCard,
    scene: Scene,
    fleet: FleetSpec,
    flight: FlightSettings,
    viewer: ViewerDirection,
) -> Placement:
    lo, hi = positions.min(axis=0), positions.max(axis=0)
    local = positions - (lo + hi) / 2
    if card.target_height_m is not None:
        height = hi[2] - lo[2]
        if height <= 0:
            raise ValueError(f"model {card.id!r} is flat along Z; set its size by spacing instead")
        scale = card.target_height_m / height
    else:
        scale = min_safe_scale(local, fleet.min_distance_m * flight.formation_spacing_factor)
    local = local * scale
    facing = _rot_x(90.0) if card.facing == "top" else np.eye(3)
    rotation = _rot_z(AUDIENCE_TURN_DEG[viewer]) @ facing @ euler_xyz(scene.rotation_deg)
    rotated = local @ rotation.T
    translation = np.array([0.0, 0.0, flight.formation_base_altitude_m - rotated[:, 2].min()])
    translation += np.asarray(scene.offset_m)
    return Placement(local, rotation, translation, scale, nearest_spacing(local))


def region_mask(region: Region, local: Array) -> npt.NDArray[np.bool_]:
    if isinstance(region, RegionAll):
        return np.ones(len(local), dtype=bool)
    if isinstance(region, RegionPlaneSplit):
        normal = np.asarray(region.normal, dtype=np.float64)
        length = np.linalg.norm(normal)
        if length == 0:
            raise ValueError("plane_split normal must not be zero")
        side = local @ (normal / length) - region.offset_m
        result: npt.NDArray[np.bool_] = side >= 0 if region.side == "positive" else side < 0
        return result
    if isinstance(region, RegionBox):
        inside: npt.NDArray[np.bool_] = np.all(
            (local >= region.min) & (local <= region.max), axis=1
        )
        return inside
    raise TypeError(f"unknown region {region!r}")


def grid_positions(count: int, spacing: float, center: tuple[float, float, float]) -> Array:
    """A near-square ground grid, row by row, centred on ``center``."""
    columns = math.ceil(math.sqrt(count))
    rows = math.ceil(count / columns)
    index = np.arange(count)
    xs = (index % columns - (columns - 1) / 2) * spacing
    ys = (index // columns - (rows - 1) / 2) * spacing
    grid = np.column_stack([xs, ys, np.zeros(count)])
    result: Array = grid + np.asarray(center)
    return result


def vertical_frames(dz: float, speed: float, accel: float, fps: int, margin: float = 1.1) -> int:
    """Frames of a synchronized vertical flight by ``dz`` (both ends included)."""
    distance = abs(dz)
    duration = (
        max(PEAK_VELOCITY * distance / speed, math.sqrt(PEAK_ACCELERATION * distance / accel))
        * margin
    )
    return max(2, math.ceil(duration * fps) + 1)


def hold_frames(hold_s: float, fps: int) -> int:
    return max(2, round(hold_s * fps) + 1)


def vertical_move(
    start: Array, dz: float, speed: float, accel: float, fps: int, margin: float = 1.1
) -> Array:
    """Synchronized vertical flight by ``dz``, quintic timing within speed and acceleration."""
    frames = vertical_frames(dz, speed, accel, fps, margin)
    s = smootherstep(np.linspace(0.0, 1.0, frames))
    lift = np.zeros((frames, 1, 3))
    lift[:, 0, 2] = dz * s
    result: Array = start[None] + lift
    return result
