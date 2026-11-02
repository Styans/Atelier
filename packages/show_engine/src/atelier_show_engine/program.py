"""A program proposed in one piece, and holds fitted to the time budget (ADR-037).

Whoever drafts a program (the assistant, later a template) says what to show and roughly
how long; the code decides the actual holds: every hold scaled by the same factor, kept
within MIN_HOLD_S..MAX_HOLD_S, so that the estimated show lasts the brief's duration.
Transitions are what the drones need and are never squeezed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from atelier_domain import HexColor
from atelier_domain.model_card import ModelKind
from atelier_show_engine.estimate import TimelineEstimate

MIN_HOLD_S = 6.0  # shorter and the audience has no time to read the figure
MAX_HOLD_S = 40.0  # longer and a static figure gets boring
HOLD_STEP_S = 0.5


class FigureDraft(BaseModel):
    """A new model card for a scene."""

    name: str = Field(min_length=1, max_length=60)
    kind: ModelKind = "mesh3d"
    description: str = Field("", max_length=600)
    image_prompt: str | None = Field(None, max_length=600)
    style: str | None = Field(None, max_length=120)
    palette: list[HexColor] = Field(default_factory=list, max_length=8)


class SceneDraft(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    hold_s: float = Field(gt=0, le=120, description="Wished hold; the code fits it to the budget")
    figure: FigureDraft | None = Field(None, description="A new card for this scene")
    model_id: str | None = Field(None, description="Or an existing card")


@dataclass
class FitResult:
    holds: dict[str, float]  # scene id -> fitted hold
    total_s: float  # estimated show length with these holds
    budget_s: float
    excess_s: float = 0.0  # still over the budget with every hold at its minimum
    slack_s: float = 0.0  # still under the budget with every hold at its maximum
    remove_scenes: int = 0  # how many scenes to drop to fit, when there is an excess
    drop_savings_s: list[float] = field(default_factory=list)  # freed per dropped scene, most first
    notes: list[str] = field(default_factory=list)

    @property
    def fits(self) -> bool:
        return self.excess_s == 0.0

    def shortest_s(self, scenes: int) -> float:
        """The shortest show with only ``scenes`` scenes left, holds at their minimum."""
        dropped = max(0, len(self.holds) - scenes)
        return self.budget_s + self.excess_s - sum(self.drop_savings_s[:dropped])


def _scaled(desired: list[float], factor: float) -> list[float]:
    return [min(MAX_HOLD_S, max(MIN_HOLD_S, d * factor)) for d in desired]


def _round(holds: list[float], available: float) -> list[float]:
    """Holds on a 0.5 s grid, never above what is available."""
    rounded = [round(h / HOLD_STEP_S) * HOLD_STEP_S for h in holds]
    while sum(rounded) > available + 1e-9:
        i = max(range(len(rounded)), key=lambda k: rounded[k])
        if rounded[i] - HOLD_STEP_S < MIN_HOLD_S:
            break
        rounded[i] -= HOLD_STEP_S
    return rounded


def fit_holds(estimate: TimelineEstimate, desired: Mapping[str, float]) -> FitResult:
    """Holds that make ``estimate`` last its budget, keeping the wished proportions."""
    scene_ids = [s.scene_id for s in estimate.segments if s.kind == "hold" and s.scene_id]
    holds_now = sum(s.duration_s for s in estimate.segments if s.kind == "hold")
    fixed = estimate.total_s - holds_now  # takeoff, transitions, return, landing
    budget = estimate.budget_s
    available = budget - fixed
    wished = [max(desired.get(i, MIN_HOLD_S), 0.1) for i in scene_ids]
    n = len(scene_ids)
    if n == 0:
        return FitResult({}, estimate.total_s, budget)

    if available <= n * MIN_HOLD_S:
        holds = [MIN_HOLD_S] * n
        excess = fixed + sum(holds) - budget
        into_scenes = [
            s.duration_s for s in estimate.segments if s.kind == "transition" and s.scene_id
        ]
        # The climb to the first figure stays whichever scene comes first: dropping a scene
        # frees one of the transitions between figures, never that one.
        transitions = sorted((t + MIN_HOLD_S for t in into_scenes[1:]), reverse=True)
        removed, saved = 0, 0.0  # dropping the costliest scenes first frees the most time
        for cost in transitions:
            if saved >= excess:
                break
            removed, saved = removed + 1, saved + cost
        return FitResult(
            dict(zip(scene_ids, holds, strict=True)),
            fixed + sum(holds),
            budget,
            excess_s=max(0.0, excess),
            remove_scenes=removed if excess > 0 else 0,
            drop_savings_s=transitions,
        )
    if available >= n * MAX_HOLD_S:
        holds = [MAX_HOLD_S] * n
        return FitResult(
            dict(zip(scene_ids, holds, strict=True)),
            fixed + sum(holds),
            budget,
            slack_s=available - sum(holds),
        )

    # One factor for all wished holds (clamped), found by bisection: the sum grows with it.
    low, high = 0.0, MAX_HOLD_S / min(wished)
    for _ in range(60):
        mid = (low + high) / 2
        if sum(_scaled(wished, mid)) < available:
            low = mid
        else:
            high = mid
    holds = _round(_scaled(wished, low), available)
    total = fixed + sum(holds)
    notes = []
    if not math.isclose(sum(holds), available, abs_tol=HOLD_STEP_S * n):
        notes.append("holds hit their limits; the show is shorter than the budget")
    return FitResult(dict(zip(scene_ids, holds, strict=True)), total, budget, notes=notes)
