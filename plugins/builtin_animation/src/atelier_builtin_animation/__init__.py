"""Built-in animation primitives: pure numpy kernels over all frames of a track."""

from __future__ import annotations

from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from atelier_plugin_sdk import AnimationPrimitive, Health, Plugin

Array = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]
Vec3 = tuple[float, float, float]

AXES: dict[str, Vec3] = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


class RotateParams(BaseModel):
    axis: Literal["x", "y", "z"] | Vec3 = Field("z", description="Rotation axis, figure-local")
    angle_deg: float = Field(
        90.0, description="Rotation at full progress; the sign follows the right-hand rule"
    )
    pivot: Vec3 = Field((0.0, 0.0, 0.0), description="Point on the axis, figure-local")


class Rotate(AnimationPrimitive[RotateParams]):
    """Turns the region around an axis as the track progresses (ease it to stop smoothly)."""

    name = "rotate"
    version = "0.1.0"
    Params = RotateParams

    def evaluate(
        self, points: Array, mask: Mask, t: Array, progress: Array, params: RotateParams
    ) -> Array:
        axis = np.asarray(AXES[params.axis] if isinstance(params.axis, str) else params.axis)
        norm = np.linalg.norm(axis)
        if norm == 0:
            raise ValueError("rotation axis must not be zero")
        k = axis / norm
        theta = np.radians(params.angle_deg) * progress
        cos, sin = np.cos(theta)[:, None, None], np.sin(theta)[:, None, None]
        pivot = np.asarray(params.pivot)
        rel = points[:, mask] - pivot
        dot = rel @ k
        rotated = (
            rel * cos
            + np.cross(np.broadcast_to(k, rel.shape), rel) * sin
            + k * dot[..., None] * (1 - cos)
        )
        out = points.copy()
        out[:, mask] = rotated + pivot
        return out


class ScalePulseParams(BaseModel):
    amplitude: float = Field(0.1, ge=0, lt=1, description="Relative size change at the peak")
    frequency_hz: float = Field(0.5, gt=0, description="Pulses per second")
    pivot: Vec3 = Field((0.0, 0.0, 0.0), description="Centre of the pulse, figure-local")


class ScalePulse(AnimationPrimitive[ScalePulseParams]):
    """Makes the region breathe: scale = 1 + amplitude * sin(2 pi f t).

    Shrinking brings drones closer; keep the amplitude below the figure's spacing margin.
    """

    name = "scale_pulse"
    version = "0.1.0"
    Params = ScalePulseParams

    def evaluate(
        self, points: Array, mask: Mask, t: Array, progress: Array, params: ScalePulseParams
    ) -> Array:
        scale = 1.0 + params.amplitude * np.sin(2 * np.pi * params.frequency_hz * t)
        pivot = np.asarray(params.pivot)
        out = points.copy()
        out[:, mask] = pivot + (points[:, mask] - pivot) * scale[:, None, None]
        return out


plugin = Plugin(components=[Rotate(), ScalePulse()], health_check=Health.ok)
