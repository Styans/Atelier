"""Scenes of the show program: a figure held on stage, reached by a transition."""

from typing import Any

from pydantic import Field

from atelier_domain.animation import AnimationSpec
from atelier_domain.base import DomainModel, Vec3


class TransitionSpec(DomainModel):
    """How the fleet flies into the scene (ADR-005)."""

    duration_s: float | None = Field(None, gt=0, description="None = minimal safe duration")
    planner: str | None = Field(None, description="None = pipeline.transition_planner")
    params: dict[str, Any] = Field(default_factory=dict)


class Scene(DomainModel):
    id: str
    title: str
    model_id: str
    hold_s: float = Field(gt=0, description="How long the figure stays after it has formed")
    transition_in: TransitionSpec = Field(default_factory=TransitionSpec)
    animation: AnimationSpec | None = None
    offset_m: Vec3 = Field(
        (0.0, 0.0, 0.0),
        description="Shift of the figure from its default anchor (bottom-centre above the origin)",
    )
    rotation_deg: Vec3 = Field(
        (0.0, 0.0, 0.0), description="Extra XYZ Euler rotation after facing the audience"
    )
