"""Results returned by plugin components."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field


class Violation(BaseModel):
    """A safety rule broken over an interval of frames (inclusive)."""

    rule: str
    severity: Literal["error", "warning"] = "error"
    frame_start: int
    frame_end: int
    t_start_s: float
    t_end_s: float
    drones: list[int] = Field(description="Zero-based drone indices")
    value: float = Field(description="Worst value over the interval")
    limit: float
    unit: str
    message: str
    scene_id: str | None = Field(None, description="Filled in by the core from the timeline")
    segment: str | None = Field(None, description="Filled in by the core from the timeline")


class RuleResult(BaseModel):
    violations: list[Violation] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    truncated: int = Field(0, description="Violations dropped because of the report limit")


@dataclass
class TransitionPlan:
    """Fly drone ``i`` from ``start[i]`` to ``targets[assignment[i]]``.

    ``positions`` includes both endpoints; ``duration_s == (len(positions) - 1) / fps``.
    """

    assignment: npt.NDArray[np.int64]
    positions: npt.NDArray[np.float64]
    duration_s: float


class ExportResult(BaseModel):
    files: list[Path]
    meta: dict[str, Any] = Field(default_factory=dict)
