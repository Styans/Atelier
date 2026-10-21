"""Built-in transition planners."""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist

from atelier_domain import FleetSpec
from atelier_plugin_sdk import Health, Plugin, RunContext, TransitionPlan, TransitionPlanner

Array = npt.NDArray[np.float64]

# Quintic (minimum-jerk) time scaling s(tau) = 10 tau^3 - 15 tau^4 + 6 tau^5:
# zero velocity and acceleration at both ends, peak ds/dtau = 15/8, peak d2s/dtau2 = 10/sqrt(3).
PEAK_VELOCITY = 15 / 8
PEAK_ACCELERATION = 10 / math.sqrt(3)


def quintic(tau: Array) -> Array:
    result: Array = tau**3 * (10 - 15 * tau + 6 * tau**2)
    return result


def minimal_duration(delta: Array, fleet: FleetSpec) -> float:
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


class HungarianDirectParams(BaseModel):
    duration_margin: float = Field(
        1.15, ge=1.0, description="Free duration = minimal safe duration x this margin"
    )
    min_duration_s: float = Field(2.0, gt=0, description="Never shorter than this")


class HungarianDirect(TransitionPlanner[HungarianDirectParams]):
    """Optimal assignment (sum of squared distances) and synchronized straight flights.

    With figures spaced at least sqrt(2) x min distance apart, such transitions cannot bring
    two drones closer than the min distance (CAPT, Turpin et al. 2014; ADR-008).
    """

    name = "hungarian_direct"
    version = "0.1.0"
    Params = HungarianDirectParams

    def plan(
        self,
        ctx: RunContext,
        start: Array,
        targets: Array,
        fleet: FleetSpec,
        fps: int,
        duration_s: float | None,
        params: HungarianDirectParams,
    ) -> TransitionPlan:
        start = np.asarray(start, dtype=np.float64)
        targets = np.asarray(targets, dtype=np.float64)
        rows, cols = linear_sum_assignment(cdist(start, targets, "sqeuclidean"))
        assignment = np.empty(len(start), dtype=np.int64)
        assignment[rows] = cols
        delta = targets[assignment] - start

        safe = minimal_duration(delta, fleet)
        if duration_s is None:
            duration = max(safe * params.duration_margin, params.min_duration_s)
            frames = math.ceil(duration * fps) + 1
        else:
            if duration_s < safe:
                ctx.log(
                    f"requested {duration_s:.2f} s is below the safe {safe:.2f} s; "
                    "the validator will report the excess",
                    "warning",
                )
            frames = max(2, round(duration_s * fps) + 1)
        tau = np.linspace(0.0, 1.0, frames)
        positions = start[None] + delta[None] * quintic(tau)[:, None, None]
        positions[-1] = targets[assignment]  # exact endpoint
        ctx.progress(1.0, f"{len(start)} drones, {(frames - 1) / fps:.2f} s")
        return TransitionPlan(
            assignment=assignment, positions=positions, duration_s=(frames - 1) / fps
        )


plugin = Plugin(components=[HungarianDirect()], health_check=Health.ok)
