"""Animation of a figure: parametric tracks built from plugin primitives (ADR-003)."""

from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from atelier_domain.base import DomainModel, Vec3

Easing = Literal["linear", "ease_in_out", "sine"]


class RegionAll(DomainModel):
    type: Literal["all"] = "all"


class RegionPlaneSplit(DomainModel):
    """Points on one side of a plane, in figure-local coordinates (centred, scaled)."""

    type: Literal["plane_split"] = "plane_split"
    normal: Vec3
    offset_m: float = 0.0
    side: Literal["positive", "negative"] = "positive"


class RegionBox(DomainModel):
    """Points inside an axis-aligned box, in figure-local coordinates."""

    type: Literal["box"] = "box"
    min: Vec3
    max: Vec3


Region = Annotated[RegionAll | RegionPlaneSplit | RegionBox, Field(discriminator="type")]


class AnimTrack(DomainModel):
    """One motion applied to (part of) a figure during a scene hold.

    ``params`` are validated against the schema of ``primitive`` by the plugin registry.
    """

    primitive: str = Field(description="animation_primitive component id, e.g. 'builtin.rotate'")
    region: Region = Field(default_factory=RegionAll)
    start_s: float = Field(0.0, ge=0, description="Start relative to the start of the hold")
    duration_s: float | None = Field(None, gt=0, description="None = until the end of the hold")
    easing: Easing = "linear"
    loop: bool = False
    params: dict[str, Any] = Field(default_factory=dict)


class ColorTrack(DomainModel):
    """A colour effect (role color_effect); compiled from phase 5 on."""

    effect: str
    region: Region = Field(default_factory=RegionAll)
    start_s: float = Field(0.0, ge=0)
    duration_s: float | None = Field(None, gt=0)
    easing: Easing = "linear"
    params: dict[str, Any] = Field(default_factory=dict)


class AnimationSpec(DomainModel):
    source_prompt: str = ""
    tracks: list[AnimTrack] = Field(default_factory=list)
    color_tracks: list[ColorTrack] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_box_regions(self) -> Self:
        tracks: list[AnimTrack | ColorTrack] = [*self.tracks, *self.color_tracks]
        for track in tracks:
            region = track.region
            if isinstance(region, RegionBox) and any(
                lo > hi for lo, hi in zip(region.min, region.max, strict=True)
            ):
                raise ValueError("box region: min must not exceed max")
        return self
